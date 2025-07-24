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
        self.indoor_address = config_dict['indoor_address']
        self.nasa_repo = nasa_repo
        self.processor = MessageProcessor(hass, self)
        self.producer = MessageProducer(hass, self)
        self.running = True
        self.data = {}
        self._added_entities = {
            PLATFORM_SENSOR: set(),
            PLATFORM_NUMBER: set(),
            PLATFORM_SWITCH: set(),
            PLATFORM_BINARY_SENSOR: set(),
            PLATFORM_SELECT: set(),
        }
        self._data_lock = asyncio.Lock()
        self._entity_adders = {}
        self._write_confirmations = {}
        self._tcp_read_task = None
        self._tcp_write_task = None
        self._tcp_polling_tasks = []
        _LOGGER.info(f"Initialized EHSSentinelCoordinator with IP: {self.ip}, Port: {self.port}, Write Mode: {self.writemode}, Polling: {self.polling}, extended_logging: {self.extended_logging}")

    def create_write_confirmation(self, msgname):
        event = asyncio.Event()
        self._write_confirmations[msgname] = event
        return event
    
    def confirm_write(self, msgname):
        event = self._write_confirmations.get(msgname)
        if event:
            event.set()
            del self._write_confirmations[msgname]

    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers = {("samsung_ehssentinel",)},
            name = "Samsung EHSSentinel",
            manufacturer = "echoDave",
            model = "EHS Sentinel",
            sw_version = "0.0.4",
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
                if category not in self._added_entities:
                    self._added_entities[category] = set()
                for key, val_dict in values.items():
                    if key not in [obj._key for obj in self._added_entities[category]]:
                        _LOGGER.debug(f"Adding new entity for {category}: {key} / {val_dict.get('nasa_name', 'Unknown')} / {val_dict.get('value', 'Unknown')}")
                        entity_cls = ENTITY_CLASS_MAP.get(category)
                        if entity_cls:
                            entity_obj = entity_cls(self, key, nasa_name=val_dict.get('nasa_name'))
                            new_entities.append(entity_obj)
                            self._added_entities[category].add(entity_obj)
                    else:
                        _LOGGER.debug(f"Entity update {category}: {key} / {val_dict.get('nasa_name', 'Unknown')} / {val_dict.get('value', 'Unknown')}")
                self.data[category].update(values)
            self.async_set_updated_data(self.data)
            
            if new_entities and category in self._entity_adders:
                self._entity_adders[category](new_entities)

    async def _async_update_data(self):
        """Fetch data from source."""
        # Hier kannst du z.B. aktuelle Daten zurückgeben oder einfach ein leeres Dict
        return self.data

    async def start_ehs_sentinel(self):
        _LOGGER.info("EHS Sentinel Integration started")
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
            await asyncio.sleep(20)

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
                    
    async def make_default_request_packet(self, poller):
        schedule_seconds = self.parse_time_string(poller['schedule'])
        _LOGGER.info(f"Setting up Poller {poller['name']} every {schedule_seconds} seconds")
        message_list = self.polling_yaml['groups'].get(poller['name'], [])

        try:
            while self.running:
                try:
                    await self.producer.read_request(message_list)
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
