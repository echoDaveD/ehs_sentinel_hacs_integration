import logging
import asyncio
import re
import socket
import yaml
import gzip
import shutil
from datetime import datetime
import traceback

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er

from .message_processor import MessageProcessor
from .message_producer import MessageProducer
from .nasa_packet import NASAPacket, AddressClassEnum 
from .const import DOMAIN, DEVICE_ID, PLATFORM_SENSOR, PLATFORM_NUMBER, PLATFORM_SWITCH, PLATFORM_BINARY_SENSOR, PLATFORM_SELECT, LEGACY_INSTANCE_NAME, CONF_NAMING_SCHEME, NAMING_SCHEME_LEGACY
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

VALID_PLATFORMS = {
    PLATFORM_SENSOR,
    PLATFORM_NUMBER,
    PLATFORM_SWITCH,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SELECT,
}
PLATFORM_LOG_ORDER = (
    PLATFORM_SENSOR,
    PLATFORM_NUMBER,
    PLATFORM_SWITCH,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SELECT,
)

_LOGGER = logging.getLogger(__name__)


class _InstanceLoggerAdapter(logging.LoggerAdapter):
    """Prepends the coordinator instance name to every log message."""

    def process(self, msg, kwargs):
        return f"[{self.extra['instance']}] {msg}", kwargs


EHS_PACKET_WORKERS = 5  # Anzahl paralleler Packet-Worker, anpassbar
EHS_PACKET_QUEUE_MAXSIZE = 100  # Maximale Queue-Größe
EHS_PACKET_QUEUE_WARN_THRESHOLD = 0.8  # 80% Warnschwelle

class EHSSentinelCoordinator(DataUpdateCoordinator):
    """Coordinator für EHS Sentinel, verwaltet Daten und Entitäten."""

    def __init__(self, hass, config_entry, config_dict, nasa_repo):
        super().__init__(hass, _LOGGER, name="EHS Sentinel Coordinator")

        self.config_entry = config_entry

        self.ip = config_dict['ip']
        self.port = config_dict['port']
        self.instance_name = config_dict.get('name') or LEGACY_INSTANCE_NAME
        self._logger = _InstanceLoggerAdapter(_LOGGER, {"instance": self.instance_name})
        self.writemode = config_dict['write_mode']
        self.polling = config_dict['polling']
        self.extended_logging = config_dict['extended_logging']
        self.polling_yaml = yaml.safe_load(config_dict['polling_yaml'])
        self.diagnostic_logs = config_dict['diagnostic_logs']
        self.indoor_address = None
        self.outdoor_address = None
        self.force_refresh = config_dict['force_refresh']
        self.nasa_repo = nasa_repo
        self.processor = MessageProcessor(hass, self)
        self.producer = MessageProducer(hass, self)
        self.running = True
        self.data = {}
        self._packet_logger = None
        self._data_lock = asyncio.Lock()
        self._entity_adders = {}
        self._write_confirmations = {}
        self._read_confirmations = {}
        self._diagnostic_task = None
        self._tcp_task = None
        self._tcp_read_task = None
        self._tcp_write_task = None
        self._tcp_reader = None
        self._tcp_writer = None
        self._tcp_polling_tasks = {}
        self._packet_queue = asyncio.Queue(maxsize=EHS_PACKET_QUEUE_MAXSIZE)
        self._packet_workers = []
        self.stats = {
            "packets_read": 0,
            "packets_processed": 0,
            "packets_processed_not_indoor_outdoor": 0,
            "packets_requested": 0,
        }
        
        preinitialized_counts = {platform: 0 for platform in PLATFORM_LOG_ORDER}
        self._naming_scheme = self._resolve_naming_scheme(hass)
        self._stats_lock = asyncio.Lock()
        self._logger.info("EHS Sentinel Coordinator Instance starting with configuration:")
        self._logger.info(f"         Instance Name: {self.config_entry.title}")
        self._logger.info(f"         Naming Scheme: {self._naming_scheme}")
        self._logger.info(f"         Version: {self.config_entry.version}")
        self._logger.info(f"         IP: {self.ip}")
        self._logger.info(f"         Port: {self.port}")
        self._logger.info(f"         Write Mode: {self.writemode}")
        self._logger.info(f"         Polling: {self.polling}")
        self._logger.info(f"         extended_logging: {self.extended_logging}")
        self._logger.info(f"         Force Refresh: {self.force_refresh}")
        # Vorinitialisiere coordinator.data mit allen entity-faehigen Eintraegen aus nasa_repo,
        # damit Plattform-Setups beim Start alle moeglichen Entities anlegen koennen.
        for key, meta in (nasa_repo.items() if nasa_repo else []):
            if not isinstance(meta, dict):
                continue

            hass_opts = meta.get("hass_opts")
            if not isinstance(hass_opts, dict):
                continue

            default_platform = hass_opts.get("default_platform")
            writable_platform = hass_opts.get("platform", {}).get("type")

            # Bevorzuge bei Writable-Items die steuerbare Plattform, sonst fallback auf default_platform.
            platform = writable_platform if hass_opts.get("writable") else default_platform
            if platform not in VALID_PLATFORMS:
                platform = default_platform
            if platform not in VALID_PLATFORMS:
                continue

            self.data.setdefault(platform, {})
            if platform == PLATFORM_NUMBER:
                val = 0
            else:
                val = None

            self.data[platform].setdefault(self.processor._normalize_name(key), {
                "value": val,
                "nasa_name": meta.get("nasa_name", key),
                "nasa_last_seen": meta.get("nasa_last_seen", None),
                "seen_once": False,
            })
            preinitialized_counts[platform] += 1

        if self.extended_logging:
            summary = ", ".join(
                f"{platform}={preinitialized_counts[platform]}"
                for platform in PLATFORM_LOG_ORDER
                if preinitialized_counts[platform] > 0
            )
            self._logger.info("Pre-initialized entities: %s", summary or "none")

    async def _inc_stat(self, key: str, value: int = 1):
        async with self._stats_lock:
            self.stats[key] += value

    def create_write_confirmation(self, msgname, value):
        event = asyncio.Event()
        self._write_confirmations[msgname] = {"event": event, "value": value}
        return event
    
    def confirm_write(self, msgname, value):
        event = self._write_confirmations.get(msgname, {}).get("event", None)
        event_value = self._write_confirmations.get(msgname, {}).get("value", None)
        
        if event is not None and event_value is not None:
            if event_value == value:
                self._logger.info(f"Confirming write for {msgname} with value: {value}, target value was: {event_value}")
                event.set()
                del self._write_confirmations[msgname]
    
    def create_read_confirmation(self, msgname):
        event = asyncio.Event()
        self._read_confirmations[msgname] = event
        return event
    
    def confirm_read(self, msgname):
        event = self._read_confirmations.get(msgname, None)
        if event:
            event.set()
            del self._read_confirmations[msgname]

    def uses_legacy_naming(self) -> bool:
        config_entry = getattr(self, "config_entry", None)
        if not config_entry:
            return True
        return self._naming_scheme == NAMING_SCHEME_LEGACY

    def _resolve_naming_scheme(self, hass) -> str:
        """Keep legacy naming for an entry if it already owns legacy entities."""
        config_entry = getattr(self, "config_entry", None)
        if not config_entry:
            return NAMING_SCHEME_LEGACY

        configured_scheme = config_entry.data.get(CONF_NAMING_SCHEME, NAMING_SCHEME_LEGACY)
        if configured_scheme == NAMING_SCHEME_LEGACY:
            return NAMING_SCHEME_LEGACY

        entity_reg = er.async_get(hass)
        existing_entities = er.async_entries_for_config_entry(entity_reg, config_entry.entry_id)
        if any(
            entity_entry.platform == DOMAIN and entity_entry.unique_id and entity_entry.unique_id.startswith(DEVICE_ID)
            for entity_entry in existing_entities
        ):
            self._logger.info(
                "Detected legacy entities for entry %s; keeping legacy naming for all entities",
                config_entry.entry_id,
            )
            return NAMING_SCHEME_LEGACY

        return configured_scheme

    def device_info(self) -> DeviceInfo:
        if self.uses_legacy_naming():
            return DeviceInfo(
                identifiers = {("samsung_ehssentinel",)},
                name = self.instance_name,
                manufacturer = "echoDave",
                model = "EHS Sentinel",
                sw_version = "2.0.0",
            )

        config_entry = getattr(self, "config_entry", None)
        entry_id = config_entry.entry_id if config_entry else None
        device_identifier = entry_id or f"{self.ip}:{self.port}"

        return DeviceInfo(
            identifiers = {(DOMAIN, device_identifier)},
            name = self.instance_name,
            manufacturer = "echoDave",
            model = "EHS Sentinel",
            sw_version = "2.0.0",
        )
    
    def register_entity_adder(self, category, adder):
        self._entity_adders[category] = adder
        self._logger.debug(f"Entity adder registered: {self._entity_adders}")

    async def update_data_safe(self, parsed):
        async with self._data_lock:
            for category, values in parsed.items():
                if category not in self.data:
                    self.data[category] = {}
                for key, val_dict in values.items():
                    current_data = self.data[category].get(key, {})
                    seen_once = current_data.get("seen_once", False) or val_dict.get("seen_once", True)
                    enriched_val_dict = {**val_dict, "seen_once": seen_once}
                    entity = current_data.get('_entity')
                    if entity is None:
                        if current_data:
                            # Entity wird statisch im Plattform-Setup erzeugt; hier nur Daten mergen.
                            self.data[category][key].update(enriched_val_dict)
                        else:
                            self._logger.debug(
                                "Ignoring runtime key not present in statically preloaded repository: %s/%s",
                                category,
                                key,
                            )
                    else:
                        # Wert direkt im Entity-Objekt aktualisieren
                        if hasattr(entity, 'update_value'):
                            entity.update_value(enriched_val_dict)
                        self.data[category][key].update(enriched_val_dict)

    async def _async_update_data(self):
        """Fetch data from source."""
        # Hier kannst du z.B. aktuelle Daten zurückgeben oder einfach ein leeres Dict
        return self.data

    async def start_ehs_sentinel(self):
        self._logger.info("Starting EHS Sentinel Coordinator..")
        self._packet_logger = await self.setup_packet_logger()
        self._tcp_task = asyncio.create_task(self._tcp_loop())
        if self.diagnostic_logs:
            try:
                if self._diagnostic_task is None:
                    self._diagnostic_task = asyncio.create_task(self._start_log_task())
            except Exception:
                self._logger.exception("Failed to start diagnostic task")
        # Starte Packet-Worker
        for _ in range(EHS_PACKET_WORKERS):
            self._packet_workers.append(asyncio.create_task(self._packet_worker()))

    async def _cancel_task(self, task: asyncio.Task | None, task_name: str, timeout: float = 5.0):
        """Cancel and await a task with timeout so shutdown cannot hang indefinitely."""
        if task is None:
            return

        if task.done():
            return

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.CancelledError:
            self._logger.info("%s cancelled", task_name)
        except asyncio.TimeoutError:
            self._logger.warning("%s did not stop within %.1f seconds", task_name, timeout)
        except Exception:
            self._logger.exception("Unexpected error while cancelling %s", task_name)

    async def _close_tcp_writer(self):
        """Close the current TCP writer to immediately unblock pending reads."""
        writer = self._tcp_writer
        if writer is None:
            return

        self._tcp_writer = None
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=5)
        except asyncio.TimeoutError:
            self._logger.warning("TCP writer did not close within 5 seconds")
        except Exception:
            # Writer may already be closed or unusable during shutdown.
            pass

        if self.producer is not None:
            self.producer.set_writer(None)

    async def _abort_tcp_reader(self):
        """Abort reader transport to force pending reader.read() futures to wake up."""
        reader = self._tcp_reader
        if reader is None:
            return

        self._tcp_reader = None
        try:
            transport = getattr(reader, "_transport", None)
            if transport is not None:
                transport.abort()
        except Exception:
            pass

    async def stop(self):
        self._logger.info("Stopping EHS Sentinel Coordinator...")
        self.running = False

        await self._close_tcp_writer()
        await self._abort_tcp_reader()

        await self._cancel_task(self._tcp_read_task, "TCP read task")
        self._tcp_read_task = None

        await self._cancel_task(self._tcp_write_task, "TCP write task")
        self._tcp_write_task = None

        for poller_name, task in list(self._tcp_polling_tasks.items()):
            await self._cancel_task(task, f"Polling task '{poller_name}'")
        self._tcp_polling_tasks.clear()

        await self._cancel_task(self._tcp_task, "TCP loop task")
        self._tcp_task = None
        
        if self._diagnostic_task:
            self._diagnostic_task.cancel()
            try:
                await self._diagnostic_task
            except asyncio.CancelledError:
                self._logger.info("Diagnostic task cancelled")

        # Stoppe Packet-Worker
        for worker in self._packet_workers:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                self._logger.info("Packet worker cancelled")
        self._packet_workers.clear()

        self.producer = None
        self.processor = None

        self._logger.info("EHS Sentinel Coordinator stopped")

    async def setup_packet_logger(self):
        return await self.hass.async_add_executor_job(self._setup_packet_logger_sync)
    
    async def _log_packet(self, message: str):
        if self._packet_logger:
            await self.hass.async_add_executor_job(
                self._packet_logger.info, message
            )
    
    def _setup_packet_logger_sync(self):
        log_dir = Path(
            self.hass.config.path("www", DOMAIN, "logs")
        )
        log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger(f"{DOMAIN}.packet_logger")
        logger.setLevel(logging.INFO)
        logger.propagate = False # Verhindert doppelte Logs

        handler = TimedRotatingFileHandler(
            log_dir / f"packet_{self.config_entry.title}.log",
            when="midnight",
            interval=1,
            backupCount=3,
            encoding="utf-8",
        )

        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s.%(msecs)03d] %(message)s",
                datefmt="%Y-%m-%d, %H:%M:%S",
            )
        )

        
        def namer(name):
            return name + ".gz"

        def rotator(source, dest):
            with open(source, "rb") as f_in:
                with gzip.open(dest, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            Path(source).unlink()  # Original löschen

        handler.namer = namer
        handler.rotator = rotator

        logger.addHandler(handler)
        return logger

    async def _tcp_loop(self):
        writer = None
        while self.running:
            try:
                self._logger.info("Attempting to connect to TCP device...")
                reader, writer = await asyncio.open_connection(self.ip, self.port)
                self._tcp_reader = reader
                self._tcp_writer = writer

                # Enable TCP keepalive so the OS detects dead peers even when we
                # never transmit (write_mode=false, polling=false).
                sock = writer.get_extra_info("socket")
                if sock is not None:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    try:
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                    except AttributeError:
                        pass  # TCP_KEEPIDLE/INTVL/CNT not available on this platform

                self.producer.set_writer(writer)
                self._tcp_read_task = asyncio.create_task(self._tcp_read(reader))
                self._tcp_write_task = asyncio.create_task(self._tcp_write())

                # Avoid awaiting _tcp_read_task directly. A direct await can keep
                # _tcp_loop in cancelling state during HA shutdown when the reader
                # does not unwind quickly enough.
                while self.running and self._tcp_read_task and not self._tcp_read_task.done():
                    await asyncio.sleep(0.5)

                if self._tcp_read_task and not self._tcp_read_task.done():
                    await self._cancel_task(self._tcp_read_task, "TCP read task")

                if self._tcp_read_task and self._tcp_read_task.done() and not self._tcp_read_task.cancelled():
                    read_exc = self._tcp_read_task.exception()
                    if read_exc is not None:
                        raise read_exc

                if not self._tcp_write_task.done():
                    self._tcp_write_task.cancel()
                    try:
                        await self._tcp_write_task
                    except asyncio.CancelledError:
                        pass
            except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
                self._logger.error(f"TCP connection failed or lost: {e}")
                await asyncio.sleep(5)  # wait before reconnect
            except asyncio.CancelledError:
                self._logger.info("TCP loop cancelled")
                await self._abort_tcp_reader()
                await self._cancel_task(self._tcp_read_task, "TCP read task")
                self._tcp_read_task = None
                await self._cancel_task(self._tcp_write_task, "TCP write task")
                self._tcp_write_task = None
                break
            except Exception as e:
                self._logger.error(f"Unexpected error in TCP loop: {e}")
                self._logger.error(traceback.format_exc())
                await asyncio.sleep(5)
            finally:
                # Always close the writer so we don't leak sockets or leave
                # zombie clients on the bridge side.
                if writer is not None:
                    try:
                        writer.close()
                        await asyncio.wait_for(writer.wait_closed(), timeout=5)
                    except Exception:
                        pass
                    writer = None
                    if self._tcp_writer is not None:
                        self._tcp_writer = None

                if self._tcp_reader is not None:
                    self._tcp_reader = None

                if self.producer is not None:
                    self.producer.set_writer(None)

        self._logger.info("TCP loop finished")

    async def _tcp_write(self):
        self._logger.info("Starting TCP write task")
        try:
            await asyncio.sleep(10)  # Initial delay before sending first request

            if self.indoor_address is None or self.outdoor_address is None:
                self._logger.info("Waiting for auto-detection of Indoor/Outdoor Unit Addresses...")
                counter = 0
                while (self.indoor_address is None or self.outdoor_address is None) and self.running:
                    await asyncio.sleep(5)
                    counter += 1
                    if counter >= 60:
                        self._logger.warning("Auto-detection of Indoor/Outdoor Unit Addresses timed out after 60 seconds.")
                        break

            if self.writemode:
                await self.request_all_writable_entities() # Request all writable entities
                await asyncio.sleep(300) # wait longer, all fsv are polled here, so we have most data available
            else:
                await asyncio.sleep(20) # Wait for initial data to be processed

            if self.polling:
                for poller in self.polling_yaml['fetch_interval']:
                    if poller['enable']:
                        poller_name = poller['name']
                        # Starte pro Poller nur einen Task, falls nicht schon laufend
                        if poller_name not in self._tcp_polling_tasks or self._tcp_polling_tasks[poller_name].done():
                            await asyncio.sleep(1)
                            task = asyncio.create_task(self.make_default_request_packet(poller=poller))
                            self._tcp_polling_tasks[poller_name] = task
        except asyncio.CancelledError:
            self._logger.info("TCP write task cancelled")
        except Exception as e:
            self._logger.error("Unexpected error in TCP write task")
            self._logger.error(f"{e}")
            self._logger.error(traceback.format_exc())

    async def request_all_writable_entities(self):
        self._logger.info("Requesting all writable entities")
        entities = []
        for entity in self.nasa_repo:
            if self.nasa_repo[entity]['hass_opts']['writable'] and self.writemode:
                self._logger.debug(f"Requesting writable entity: {entity}")
                entities.append(entity)

        if len(entities) > 0:
            try:
                await self.producer.read_request(entities, retry_mode=True)
                await self._inc_stat("packets_requested", len(entities))
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                self._logger.warning(f"TCP connection lost while requesting writable entities: {e}")
            except Exception as e:
                self._logger.error(f"Unexpected error while requesting writable entities: {e}")
                self._logger.error(traceback.format_exc())
        
        self._logger.info("Requesting all writable entities completed")
                    
    async def make_default_request_packet(self, poller):
        schedule_seconds = self.parse_time_string(poller['schedule'])
        self._logger.info(f"Setting up Poller {poller['name']} every {schedule_seconds} seconds")
        message_list = self.polling_yaml['groups'].get(poller['name'], [])

        try:
            while self.running:
                try:
                    await self.producer.read_request(message_list, retry_mode=True)
                    await self._inc_stat("packets_requested")
                except (ConnectionResetError, BrokenPipeError, OSError) as e:
                    self._logger.warning(f"Polling '{poller['name']}': TCP connection lost: {e}")
                    break  # raus aus Poller Task – wird neu gestartet vom Reconnect-Loop
                except Exception as e:
                    self._logger.error(f"Polling '{poller['name']}': Unexpected error")
                    self._logger.error(f"Error: {e}")
                    self._logger.error(traceback.format_exc())

                await asyncio.sleep(schedule_seconds)
                self._logger.debug(f"Refreshed Poller {poller['name']}")
        except asyncio.CancelledError:
            self._logger.info(f"Polling '{poller['name']}' task cancelled")

    def parse_time_string(self, time_str: str) -> int:
        match = re.match(r'^(\d+)([smh])$', time_str.strip(), re.IGNORECASE)
        if not match:
            raise ValueError("Invalid time format. Use '10s', '10m', or '10h'.")
        
        value, unit = int(match.group(1)), match.group(2).lower()
        
        conversion_factors = {
            's': 1,   # seconds
            'm': 60,  # minutes
            'h': 3600 # hours
        }
    
        return value * conversion_factors[unit]

    async def _tcp_read(self, reader: asyncio.StreamReader):
        self._logger.info("Starting TCP read task")
        prev_byte = 0x00
        packet_started = False
        data = bytearray()
        packet_size = 0
        idle_timeouts = 0
        try:
            while self.running:
                try:
                    current_byte = await asyncio.wait_for(reader.read(1), timeout=1)
                    idle_timeouts = 0
                except asyncio.TimeoutError:
                    idle_timeouts += 1
                    if idle_timeouts >= 30:
                        _LOGGER.warning("TCP read: No data received for 30 s, assuming dead connection")
                        break
                    continue
                if not current_byte:
                    self._logger.warning("TCP read: Connection closed by remote")
                    break  # Verbindung beendet

                if current_byte:
                    if packet_started:
                        data.extend(current_byte)
                        if len(data) == 3:
                            packet_size = ((data[1] << 8) | data[2]) + 2

                        if packet_size <= len(data):
                            await self._inc_stat("packets_read")
                            if current_byte == b'\x34':
                                asyncio.create_task(self.process_buffer(data))
                            else:
                                self._logger.debug("Packet does not end properly, skip it...")

                            data = bytearray()
                            packet_started = False

                    if current_byte == b'\x00' and prev_byte == b'\x32':
                        packet_started = True
                        data.extend(prev_byte)
                        data.extend(current_byte)

                    prev_byte = current_byte
        except asyncio.CancelledError:
            self._logger.info("TCP read task cancelled")
        except Exception as e:
            self._logger.error(f"Error in TCP read loop: {e}")
            self._logger.error(traceback.format_exc())

            #await asyncio.sleep(0.01)  # Short break to reduce CPU load

        self._logger.info("TCP connection closed, EHS Sentinel integration terminated")

    async def _packet_worker(self):
        while self.running:
            try:
                buffer = await self._packet_queue.get()
                try:
                    await asyncio.wait_for(self.process_packet(buffer), timeout=3)
                except asyncio.TimeoutError:
                    self._logger.warning("process_packet timeout, packet verworfen")
                except Exception:
                    self._logger.exception("Error in packet worker")
                finally:
                    self._packet_queue.task_done()
            except asyncio.CancelledError:
                break

    async def process_buffer(self, buffer):
        if buffer and len(buffer) > 14:
            for i in range(0, len(buffer)):
                if buffer[i] == 0x32:
                    if (len(buffer[i:]) > 14):
                        # Queue-Überwachung
                        qsize = self._packet_queue.qsize()
                        if qsize >= EHS_PACKET_QUEUE_MAXSIZE * EHS_PACKET_QUEUE_WARN_THRESHOLD:
                            self._logger.warning(f"Packet-Queue zu {qsize}/{EHS_PACKET_QUEUE_MAXSIZE} belegt!")
                        if qsize >= EHS_PACKET_QUEUE_MAXSIZE:
                            self._logger.error("Packet-Queue voll, Packet verworfen!")
                            return 
                        await self._packet_queue.put(buffer[i:])
                    else:
                        self._logger.debug(f"Packet too short, skip processing: {len(buffer)}")
                    break

    async def process_packet(self, buffer):
        try:
            nasa_packet = NASAPacket()
            nasa_packet.parse(buffer)
            self._logger.debug(f"Received Packet: {nasa_packet}")
            if nasa_packet.packet_source_address_class in (AddressClassEnum.Outdoor, AddressClassEnum.Indoor):
                if self.indoor_address is None and nasa_packet.packet_source_address_class == AddressClassEnum.Indoor:
                    self.indoor_address = {'class': nasa_packet.packet_source_address_class.value, 'channel': nasa_packet.packet_source_channel, 'address': nasa_packet.packet_source_address}
                    self._logger.info(f"Auto-detected Indoor Unit Address: {self.indoor_address['class']:02X}.{self.indoor_address['channel']:02X}.{self.indoor_address['address']:02X}")
                if self.outdoor_address is None and nasa_packet.packet_source_address_class == AddressClassEnum.Outdoor:
                    self.outdoor_address = {'class': nasa_packet.packet_source_address_class.value, 'channel': nasa_packet.packet_source_channel, 'address': nasa_packet.packet_source_address}
                    self._logger.info(f"Auto-detected Outdoor Unit Address: {self.outdoor_address['class']:02X}.{self.outdoor_address['channel']:02X}.{self.outdoor_address['address']:02X}")
                
                # Schreibe Packet logs
                await self._log_packet(
                        " ".join(f"{b:02X}" for b in buffer)
                    )

                # verarbeite die Nachricht
                await self.processor.process_message(nasa_packet)
                
            elif self.extended_logging:
                await self._inc_stat("packets_processed_not_indoor_outdoor")
                if( nasa_packet.packet_source_address_class == AddressClassEnum.WiFiKit and all([tmpmsg.packet_message==0 for tmpmsg in nasa_packet.packet_messages])):
                    pass
                else:
                    self._logger.info(f"[extended_logging] Packet from {nasa_packet.packet_source_address_class} \n {nasa_packet}")
            else:
                await self._inc_stat("packets_processed_not_indoor_outdoor")
                self._logger.debug(f"Packet not from Outdoor/Indoor Unit: {nasa_packet}")
            await self._inc_stat("packets_processed")
        except Exception as e:
            if self.extended_logging:
                self._logger.warning(f"Error while processing the Packet: {e}")
                self._logger.warning(f"                  Complete Packet: {[hex(x) for x in buffer]}")
                self._logger.warning(traceback.format_exc())

    def is_valid_rawvalue(self, rawvalue: bytes) -> bool:
        return all(0x20 <= b <= 0x7E or b in (0x00, 0xFF) for b in rawvalue)

    async def determine_value(self, rawvalue, msgname, packet_message_type):
        nasa_repo = self.nasa_repo
        if packet_message_type == 3:
            value = ""
            if self.is_valid_rawvalue(rawvalue[1:-1]):
                for byte in rawvalue[1:-1]:
                    if byte != 0x00 and byte != 0xFF:
                        char = chr(byte) if 32 <= byte <= 126 else f"{byte}"
                        value += char
                    else:
                        value += " "
                value = value.strip()
            else:
                value = "".join([f"{int(x)}" for x in rawvalue])

            # self._logger.warning(
            #     "Received String Message: instance=%s msg=%s raw=%s/%s/%s",
            #     self.instance_name,
            #     msgname,
            #     rawvalue,
            #     rawvalue.hex(),
            #     value,
            # )
            # raw_hex = rawvalue.hex()
            # payload_len = len(rawvalue)
            # header_bytes = rawvalue[:4]
            # data_bytes = rawvalue[4:]
            # header_hex = header_bytes.hex()
            # header_value = int.from_bytes(header_bytes, byteorder="big", signed=False) if len(header_bytes) == 4 else None
            # data_hex = data_bytes.hex()

            # # C-style string: read until first null byte.
            # c_style = rawvalue.split(b"\x00", 1)[0].decode("ascii", errors="ignore")

            # # Pascal-style string: first byte is the length.
            # pascal_len = rawvalue[0] if payload_len >= 1 else 0
            # pascal_end = min(1 + pascal_len, payload_len)
            # pascal_style = rawvalue[1:pascal_end].decode("ascii", errors="ignore") if payload_len >= 1 else ""

            # # Hybrid style: Pascal content followed by a null terminator.
            # hybrid_valid = payload_len > pascal_end and rawvalue[pascal_end] == 0x00 if payload_len >= 1 else False
            # hybrid_style = rawvalue[1:pascal_end].decode("ascii", errors="ignore") if hybrid_valid else ""

            # self._logger.warning(
            #     "[STRUCT DEBUG] instance=%s msg=%s raw_hex=%s len=%s bytes=%s header_hex=%s header_u32=%s data_hex=%s data_len=%s c_style='%s' pascal_len=%s pascal_style='%s' hybrid_valid=%s hybrid_style='%s' rendered='%s'",
            #     self.instance_name,
            #     msgname,
            #     raw_hex,
            #     payload_len,
            #     list(rawvalue),
            #     header_hex,
            #     header_value,
            #     data_hex,
            #     len(data_bytes),
            #     c_style,
            #     pascal_len,
            #     pascal_style,
            #     hybrid_valid,
            #     hybrid_style,
            #     value,
            # )
        else:
            if 'arithmetic' in nasa_repo[msgname]:
                arithmetic = nasa_repo[msgname]['arithmetic'].replace("value", 'packed_value')
            else:
                arithmetic = ''
            packed_value = int.from_bytes(rawvalue, byteorder='big', signed=True)
            if len(arithmetic) > 0:
                try:
                    value = eval(arithmetic)
                except Exception:
                    value = packed_value
            else:
                value = packed_value
            value = round(value, 3)
            if 'type' in nasa_repo[msgname]:
                if nasa_repo[msgname]['type'] == 'ENUM':
                    if 'enum' in nasa_repo[msgname]:
                        value = nasa_repo[msgname]['enum'][int.from_bytes(rawvalue, byteorder='big')]
                    else:
                        value = f"Unknown enum value: {value}"
        return value
    
    async def _start_log_task(self):
        """Startet die tasks zum loggen der Diagnostic Task."""
        while self.running:
            await self._log_task_stats()
            await asyncio.sleep(60)

    async def _log_task_stats(self):
        """Loggt die Anzahl der Sentinel tasks sowie die Tasks selbst, außerdem die Queue-Größe und einige Statistiken."""
        try:
            tasks = [t for t in asyncio.all_tasks() if "EHSSentinelCoordinator" in str(t.get_coro())]
            total = len(tasks)
            # collect top coroutine names
            coro_counts = {}
            for t in tasks:
                try:
                    coro = t.get_coro()
                    name = getattr(coro, "__qualname__", repr(coro))
                except Exception:
                    name = repr(t)
                coro_counts[name] = coro_counts.get(name, 0) + 1
            top = sorted(coro_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            self._logger.info(
                "[EHS-Sentinel Diagnostics] Task Overview: tasks=%s top=%s",
                total,
                top,
            )
            self._logger.info(f"[EHS-Sentinel Diagnostics] Current Packet Queue Size: {self._packet_queue.qsize()}")
            async with self._stats_lock:
                stats_snapshot = dict(self.stats)
            self._logger.info(
                "[EHS-Sentinel Diagnostics] MessageCounters: read=%s processed=%s not_from_indoor/outdoor=%s requested=%s",
                stats_snapshot["packets_read"],
                stats_snapshot["packets_processed"],
                stats_snapshot["packets_processed_not_indoor_outdoor"],
                stats_snapshot["packets_requested"],
            )
        except Exception:
            self._logger.exception("Error while collecting diagnostics")
