import logging
import asyncio
import re
import yaml
import traceback

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.entity import DeviceInfo

from .message_processor import MessageProcessor
from .message_producer import MessageProducer
from .nasa_packet import NASAPacket, AddressClassEnum 
from .sensor import EHSSentinelSensor
from .number import EHSSentinelNumber
from .switch import EHSSentinelSwitch
from .binary_sensor import EHSSentinelBinarySensor
from .select import EHSSentinelSelect
from .const import DOMAIN, DEVICE_ID, PLATFORM_SENSOR, PLATFORM_NUMBER, PLATFORM_SWITCH, PLATFORM_BINARY_SENSOR, PLATFORM_SELECT
from homeassistant.helpers.entity import async_generate_entity_id

ENTITY_CLASS_MAP = {
    PLATFORM_SENSOR: EHSSentinelSensor,
    PLATFORM_NUMBER: EHSSentinelNumber,
    PLATFORM_SWITCH: EHSSentinelSwitch,
    PLATFORM_BINARY_SENSOR: EHSSentinelBinarySensor,
    PLATFORM_SELECT: EHSSentinelSelect,
}

_LOGGER = logging.getLogger(__name__)

class EHSSentinelCoordinator(DataUpdateCoordinator):
    """Coordinator für EHS Sentinel, verwaltet Daten und Entitäten."""

    def __init__(self, hass, config_dict, nasa_repo):
        super().__init__(hass, _LOGGER, name="EHS Sentinel Coordinator")
        self.ip = config_dict['ip']
        self.port = config_dict['port']
        self.writemode = config_dict['write_mode']
        self.polling = config_dict['polling']
        self.extended_logging = config_dict['extended_logging']
        self.polling_yaml = yaml.safe_load(config_dict['polling_yaml'])
        self.indoor_address = None
        self.outdoor_address = None
        self.force_refresh = config_dict['force_refresh']
        self.nasa_repo = nasa_repo
        self.processor = MessageProcessor(hass, self)
        self.producer = MessageProducer(hass, self)
        self.running = True
        self.data = {}
        self._data_lock = asyncio.Lock()
        self._entity_adders = {}
        self._write_confirmations = {}
        self._read_confirmations = {}
        self._tcp_read_task = None
        self._tcp_write_task = None
        self._tcp_polling_tasks = []
        _LOGGER.info(f"Initialized EHSSentinelCoordinator with IP: {self.ip}, Port: {self.port}, Write Mode: {self.writemode}, Polling: {self.polling}, extended_logging: {self.extended_logging}, Force Refresh: {self.force_refresh}")
        # Vorinitialisiere coordinator.data mit allen bekannten Einträgen aus nasa_repo die mit NASA_EHSSENTINEL_ beginnen,
        # damit Plattform-Setups beim Start Entities anlegen können.
        # Erwartet: nasa_repo[key]['hass_opts']['platform'] enthält PLATFORM_* oder ähnliches.
        for key, meta in (nasa_repo.items() if nasa_repo else []):
            if key.startswith("NASA_EHSSENTINEL_"):
                hass_opts = meta.get("hass_opts", {})
                platform = hass_opts.get("platform", {}).get("type")

                if platform is None or hass_opts.get("writable") is False:
                    platform = hass_opts.get("default_platform", None)

                if platform is not None:
                    self.data.setdefault(platform, {})
                    # lege Platzhalter mit lesbaren Default-Attributen an
                    self.data[platform].setdefault(self.processor._normalize_name(key), {
                        "value": None,
                        "nasa_name": meta.get("nasa_name", key),
                        "nasa_last_seen": None,
                    })
                    


    def create_write_confirmation(self, msgname, value):
        event = asyncio.Event()
        self._write_confirmations[msgname] = {"event": event, "value": value}
        return event
    
    def confirm_write(self, msgname, value):
        event = self._write_confirmations.get(msgname, {}).get("event", None)
        event_value = self._write_confirmations.get(msgname, {}).get("value", None)
        
        if event is not None and event_value is not None:
            if event_value == value:
                _LOGGER.info(f"Confirming write for {msgname} with value: {value}, target value was: {event_value}")
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

    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers = {("samsung_ehssentinel",)},
            name = "Samsung EHSSentinel",
            manufacturer = "echoDave",
            model = "EHS Sentinel",
            sw_version = "1.0.3",
        )
    
    def register_entity_adder(self, category, adder):
        self._entity_adders[category] = adder
        _LOGGER.debug(f"Entity adder registered: {self._entity_adders}")

    async def update_data_safe(self, parsed):
        async with self._data_lock:
            new_entities = []
            for category, values in parsed.items():
                if category not in self.data:
                    self.data[category] = {}
                for key, val_dict in values.items():
                    if key not in self.data[category]:
                        _LOGGER.debug(f"Adding new entity for {category}: {key} / {val_dict.get('nasa_name', 'Unknown')} / {val_dict.get('value', 'Unknown')}")
                        entity_cls = ENTITY_CLASS_MAP.get(category)
                        if entity_cls:
                            entity_obj = entity_cls(self, key, nasa_name=val_dict.get('nasa_name'))
                            base_id = f"{DEVICE_ID.lower()}_{key.lower()}"
                            entity_id = async_generate_entity_id(
                                category + ".{}",
                                base_id,
                                self.hass.states.async_entity_ids(category)
                            )
                            entity_obj.entity_id = entity_id  # explizit hier setzen
                            new_entities.append(entity_obj)
                    else:
                        if self.data[category].get(key).get('value') != val_dict.get('value'):
                            _LOGGER.debug(f"Entity update {category}.{key} ({val_dict.get('nasa_name', 'Unknown')}) value: {self.data[category].get(key).get('value')} -> {val_dict.get('value', 'Unknown')}")
                self.data[category].update(values)
            self.async_set_updated_data(self.data)
            
            if new_entities and category in self._entity_adders:
                self._entity_adders[category](new_entities)

    async def _async_update_data(self):
        """Fetch data from source."""
        # Hier kannst du z.B. aktuelle Daten zurückgeben oder einfach ein leeres Dict
        return self.data

    async def start_ehs_sentinel(self):
        _LOGGER.info("Starting EHS Sentinel Coordinator..")
        self._tcp_task = asyncio.create_task(self._tcp_loop())
    
    async def stop(self):
        _LOGGER.info("Stopping EHS Sentinel Coordinator...")
        self.running = False

        if self._tcp_task:
            self._tcp_task.cancel()
            try:
                await self._tcp_task
            except asyncio.CancelledError:
                _LOGGER.info("TCP task cancelled")

        for task in self._tcp_polling_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                _LOGGER.info("Polling task cancelled")

        self.producer = None
        self.processor = None

        _LOGGER.info("EHS Sentinel Coordinator stopped")
        

    async def _tcp_loop(self):
        
        while self.running:
            try:
                _LOGGER.info("Attempting to connect to TCP device...")
                reader, writer = await asyncio.open_connection(self.ip, self.port)
                self.producer.set_writer(writer)
                self._tcp_read_task = asyncio.create_task(self._tcp_read(reader))
                self._tcp_write_task = asyncio.create_task(self._tcp_write())

                await asyncio.gather(self._tcp_read_task, self._tcp_write_task)
            except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
                _LOGGER.error(f"TCP connection failed or lost: {e}")
                await asyncio.sleep(5)  # wait before reconnect
            except asyncio.CancelledError:
                _LOGGER.info("TCP loop cancelled")
                break
            except Exception as e:
                _LOGGER.error(f"Unexpected error in TCP loop: {e}")
                _LOGGER.error(traceback.format_exc())
                await asyncio.sleep(5)

        _LOGGER.info("TCP loop finished")

    async def _tcp_write(self):
        _LOGGER.info("Starting TCP write task")
        try:
            await asyncio.sleep(10)  # Initial delay before sending first request

            if self.indoor_address is None or self.outdoor_address is None:
                _LOGGER.info("Waiting for auto-detection of Indoor/Outdoor Unit Addresses...")
                counter = 0
                while (self.indoor_address is None or self.outdoor_address is None) and self.running:
                    await asyncio.sleep(5)
                    counter += 1
                    if counter >= 60:
                        _LOGGER.warning("Auto-detection of Indoor/Outdoor Unit Addresses timed out after 60 seconds.")
                        break

            if self.writemode:
                await self.request_all_writable_entities() # Request all writable entities
                await asyncio.sleep(300) # wait longer, all fsv are polled here, so we have most data available
            else:
                await asyncio.sleep(20) # Wait for initial data to be processed

            if self.polling:
                for poller in self.polling_yaml['fetch_interval']:
                    if poller['enable']:
                        await asyncio.sleep(1)
                        task = asyncio.create_task(self.make_default_request_packet(poller=poller))
                        self._tcp_polling_tasks.append(task)
        except asyncio.CancelledError:
            _LOGGER.info("TCP write task cancelled")
        except Exception as e:
            _LOGGER.error("Unexpected error in TCP write task")
            _LOGGER.error(f"{e}")
            _LOGGER.error(traceback.format_exc())

    async def request_all_writable_entities(self):
        _LOGGER.info("Requesting all writable entities")
        entities = []
        for entity in self.nasa_repo:
            if self.nasa_repo[entity]['hass_opts']['writable'] and self.writemode:
                _LOGGER.debug(f"Requesting writable entity: {entity}")
                entities.append(entity)

        if len(entities) > 0:
            try:
                await self.producer.read_request(entities, retry__mode=True)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                _LOGGER.warning(f"TCP connection lost while requesting writable entities: {e}")
            except Exception as e:
                _LOGGER.error(f"Unexpected error while requesting writable entities: {e}")
                _LOGGER.error(traceback.format_exc())
        
        _LOGGER.info("Requesting all writable entities completed")
                    
    async def make_default_request_packet(self, poller):
        schedule_seconds = self.parse_time_string(poller['schedule'])
        _LOGGER.info(f"Setting up Poller {poller['name']} every {schedule_seconds} seconds")
        message_list = self.polling_yaml['groups'].get(poller['name'], [])

        try:
            while self.running:
                try:
                    await self.producer.read_request(message_list, retry__mode=True)
                except (ConnectionResetError, BrokenPipeError, OSError) as e:
                    _LOGGER.warning(f"Polling '{poller['name']}': TCP connection lost: {e}")
                    break  # raus aus Poller Task – wird neu gestartet vom Reconnect-Loop
                except Exception as e:
                    _LOGGER.error(f"Polling '{poller['name']}': Unexpected error")
                    _LOGGER.error(f"Error: {e}")
                    _LOGGER.error(traceback.format_exc())

                await asyncio.sleep(schedule_seconds)
                _LOGGER.debug(f"Refreshed Poller {poller['name']}")
        except asyncio.CancelledError:
            _LOGGER.info(f"Polling '{poller['name']}' task cancelled")

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
        _LOGGER.info("Starting TCP read task")
        prev_byte = 0x00
        packet_started = False
        data = bytearray()
        packet_size = 0
        try:
            while self.running:
                current_byte = await reader.read(1) 
                if not current_byte:
                    _LOGGER.warning("TCP read: Connection closed by remote")
                    break  # Verbindung beendet

                if current_byte:
                    if packet_started:
                        data.extend(current_byte)
                        if len(data) == 3:
                            packet_size = ((data[1] << 8) | data[2]) + 2

                        if packet_size <= len(data):

                            if current_byte == b'\x34':
                                asyncio.create_task(self.process_buffer(data))
                            else:
                                _LOGGER.debug("Packet does not end properly, skip it...")

                            _LOGGER.debug(f"Processed packet (int): {data}")
                            _LOGGER.debug(f"Processed packet (hex): {data.hex()}")
                            _LOGGER.debug(f"Processed packet (bytewise): {[hex(x) for x in data]}")

                            data = bytearray()
                            packet_started = False

                    if current_byte == b'\x00' and prev_byte == b'\x32':
                        packet_started = True
                        data.extend(prev_byte)
                        data.extend(current_byte)

                    prev_byte = current_byte
        except asyncio.CancelledError:
            _LOGGER.info("TCP read task cancelled")
        except Exception as e:
            _LOGGER.error(f"Error in TCP read loop: {e}")
            _LOGGER.error(traceback.format_exc())

            #await asyncio.sleep(0.01)  # Short break to reduce CPU load

        _LOGGER.info("TCP connection closed, EHS Sentinel integration terminated")

    async def process_buffer(self, buffer):
        if buffer and len(buffer) > 14:
            for i in range(0, len(buffer)):
                if buffer[i] == 0x32:
                    if (len(buffer[i:]) > 14):
                        asyncio.create_task(self.process_packet(buffer[i:]))
                    else:
                        _LOGGER.debug(f"Packet too short, skip processing: {len(buffer)}")
                    break

    async def process_packet(self, buffer):
        try:
            nasa_packet = NASAPacket()
            nasa_packet.parse(buffer)
            if nasa_packet.packet_source_address_class in (AddressClassEnum.Outdoor, AddressClassEnum.Indoor):
                if self.indoor_address is None and nasa_packet.packet_source_address_class == AddressClassEnum.Indoor:
                    self.indoor_address = {'class': nasa_packet.packet_source_address_class.value, 'channel': nasa_packet.packet_source_channel, 'address': nasa_packet.packet_source_address}
                    _LOGGER.info(f"Auto-detected Indoor Unit Address: {self.indoor_address['class']:02X}.{self.indoor_address['channel']:02X}.{self.indoor_address['address']:02X}")
                if self.outdoor_address is None and nasa_packet.packet_source_address_class == AddressClassEnum.Outdoor:
                    self.outdoor_address = {'class': nasa_packet.packet_source_address_class.value, 'channel': nasa_packet.packet_source_channel, 'address': nasa_packet.packet_source_address}
                    _LOGGER.info(f"Auto-detected Outdoor Unit Address: {self.outdoor_address['class']:02X}.{self.outdoor_address['channel']:02X}.{self.outdoor_address['address']:02X}")
                await self.processor.process_message(nasa_packet)
            elif self.extended_logging:
                if( nasa_packet.packet_source_address_class == AddressClassEnum.WiFiKit and all([tmpmsg.packet_message==0 for tmpmsg in nasa_packet.packet_messages])):
                    pass
                else:
                    _LOGGER.info(f"[extended_logging] Packet from {nasa_packet.packet_source_address_class} \n {nasa_packet}")
            else:
                _LOGGER.debug(f"Packet not from Outdoor/Indoor Unit: {nasa_packet}")
        except Exception as e:
            _LOGGER.warning(f"Error while processing the Packet: {e}")
            _LOGGER.warning(f"                  Complete Packet: {[hex(x) for x in buffer]}")
            _LOGGER.warning(traceback.format_exc())

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

            logging.debug(f"Received String Message: {msgname} with raw value: {rawvalue}/{rawvalue.hex()}/{value}")
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