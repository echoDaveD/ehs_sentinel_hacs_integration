import logging
import asyncio
import json
import os
import yaml
import traceback

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .message_processor import MessageProcessor
#from .message_producer import MessageProducer
from .nasa_packet import NASAPacket, AddressClassEnum 
from .sensor import EHSSentinelSensor
from .number import EHSSentinelNumber
from .switch import EHSSentinelSwitch
from .binary_sensor import EHSSentinelBinarySensor
from .select import EHSSentinelSelect

ENTITY_CLASS_MAP = {
    "sensor": EHSSentinelSensor,
    "number": EHSSentinelNumber,
    "switch": EHSSentinelSwitch,
    "binary_sensor": EHSSentinelBinarySensor,
    "select": EHSSentinelSelect,
}

_LOGGER = logging.getLogger(__name__)

class EHSSentinelCoordinator(DataUpdateCoordinator):
    """Coordinator für EHS Sentinel, verwaltet Daten und Entitäten."""

    def __init__(self, hass, ip, port, nasa_repo):
        super().__init__(hass, _LOGGER, name="EHS Sentinel Coordinator")
        self.ip = ip
        self.port = port
        self.nasa_repo = nasa_repo
        self.processor = MessageProcessor(hass, self)
        self.producer = None
        self.running = True
        self.data = {}
        self._added_entities = {
            "sensors": set(),
            "numbers": set(),
            "switches": set(),
            "binary_sensors": set(),
            "options": set(),
        }
        self._data_lock = asyncio.Lock()
        self._entity_adders = {}

    @property
    def device_info(self):
        return {
            "identifiers": {("samsung_ehssentinel_hacs", self.ip)},  # oder eine andere eindeutige ID
            "name": "Samsung EHS",
            "manufacturer": "Samsung",
            "model": "Mono HQ Quiet",
            "sw_version": "1.0.0",
        }
    
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
                for key in values:
                    if key not in self._added_entities[category]:
                        entity_cls = ENTITY_CLASS_MAP.get(category)
                        if entity_cls:
                            new_entities.append(entity_cls(self, key))
                            self._added_entities[category].add(key)
                self.data[category].update(values)
            self.async_set_updated_data(self.data)
            # Neue Entitäten hinzufügen (nur wenn welche da sind)
            if new_entities and category in self._entity_adders:
                self._entity_adders[category](new_entities)

    async def _async_update_data(self):
        """Fetch data from source."""
        # Hier kannst du z.B. aktuelle Daten zurückgeben oder einfach ein leeres Dict
        return self.data

    async def start_ehs_sentinel(self):
        _LOGGER.info("EHS Sentinel Integration gestartet")
        self._tcp_task = asyncio.create_task(self._tcp_loop())

    async def _tcp_loop(self):
        reader, writer = await asyncio.open_connection(self.ip, self.port)
        #self.producer = MessageProducer(writer=writer)
        await asyncio.gather(
            self._tcp_read(reader)
            )
        #asyncio.create_task(self._tcp_write())

    async def _tcp_read(self, reader: asyncio.StreamReader):
        prev_byte = 0x00
        packet_started = False
        data = bytearray()
        packet_size = 0

        while self.running:
            current_byte = await reader.read(1) 
            if current_byte:
                if packet_started:
                    data.extend(current_byte)
                    if len(data) == 3:
                        packet_size = ((data[1] << 8) | data[2]) + 2

                    if packet_size <= len(data):

                        if current_byte == b'\x34':
                            asyncio.create_task(self.process_buffer(data))
                        else:
                            _LOGGER.debug("Paket nicht abgeschlossen, verwerfe das Paket...")

                        _LOGGER.debug(f"Verarbeitetes Pake (int): {data}")
                        _LOGGER.debug(f"Verarbeitetes Pake (hex): {data.hex()}")
                        _LOGGER.debug(f"Verarbeitetes Pake (bytewise): {[hex(x) for x in data]}")

                        data = bytearray()
                        packet_started = False

                if current_byte == b'\x00' and prev_byte == b'\x32':
                    packet_started = True
                    data.extend(prev_byte)
                    data.extend(current_byte)

                prev_byte = current_byte

            await asyncio.sleep(0.01)  # Kurze Pause, um CPU-Last zu reduzieren

        _LOGGER.info("TCP-Verbindung geschlossen, EHS Sentinel Integration beendet")

    async def _tcp_write(self):
        # Hier ggf. Polling-Logik einbauen, falls benötigt
        pass

    async def process_buffer(self, buffer):
        if buffer and len(buffer) > 14:
            for i in range(0, len(buffer)):
                if buffer[i] == 0x32:
                    if (len(buffer[i:]) > 14):
                        asyncio.create_task(self.process_packet(buffer[i:]))
                    else:
                        _LOGGER.debug(f"Paket zu kurz, überspringe Verarbeitung: {len(buffer)}")
                    break

    async def process_packet(self, buffer):
        try:
            nasa_packet = NASAPacket()
            nasa_packet.parse(buffer)
            if nasa_packet.packet_source_address_class in (AddressClassEnum.Outdoor, AddressClassEnum.Indoor):
                await self.processor.process_message(nasa_packet)
            else:
                _LOGGER.debug(f"Paket nicht von Outdoor/Indoor Unit: {nasa_packet}")
        except Exception as e:
            _LOGGER.warning(f"Fehler beim Verarbeiten des Pakets: {e}")
            _LOGGER.warning(f"                   Complete Packet: {[hex(x) for x in buffer]}")
            _LOGGER.warning(traceback.format_exc())

    async def stop(self):
        _LOGGER.info("Stopping EHS Sentinel Coordinator")
        self.running = False