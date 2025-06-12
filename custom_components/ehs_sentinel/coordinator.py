import logging
import asyncio
import json
import os

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .message_processor import MessageProcessor
from .message_producer import MessageProducer

_LOGGER = logging.getLogger(__name__)

NASA_REPO_PATH = os.path.join(os.path.dirname(__file__), "nasa_repo.json")  # Passe ggf. den Pfad an

class EHSSentinelCoordinator(DataUpdateCoordinator):
    """Coordinator für EHS Sentinel, verwaltet Daten und Entitäten."""

    def __init__(self, hass, ip, port):
        super().__init__(hass, _LOGGER, name="EHS Sentinel Coordinator")
        self.ip = ip
        self.port = port
        self.nasa_repo = self._load_nasa_repo()
        self.processor = MessageProcessor(hass, self)
        self.producer = None
        self.running = True

    def _load_nasa_repo(self):
        try:
            with open(NASA_REPO_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            _LOGGER.error(f"Fehler beim Laden der NASA-Repo: {e}")
            return {}

    async def start_ehs_sentinel(self):
        _LOGGER.info("EHS Sentinel Integration gestartet")
        await self._tcp_loop()

    async def _tcp_loop(self):
        reader, writer = await asyncio.open_connection(self.ip, self.port)
        self.producer = MessageProducer(writer=writer)
        asyncio.create_task(self._tcp_read(reader))
        asyncio.create_task(self._tcp_write())

    async def _tcp_read(self, reader):
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
                            await self.process_buffer(data)
                            data = bytearray()
                            packet_started = False
                        else:
                            data = bytearray()
                            packet_started = False
                if current_byte == b'\x00' and prev_byte == b'\x32':
                    packet_started = True
                    data.extend(prev_byte)
                    data.extend(current_byte)
                prev_byte = current_byte

    async def _tcp_write(self):
        # Hier ggf. Polling-Logik einbauen, falls benötigt
        pass

    async def process_buffer(self, buffer):
        if buffer and len(buffer) > 14:
            for i in range(0, len(buffer)):
                if buffer[i] == 0x32:
                    if (len(buffer[i:]) > 14):
                        await self.process_packet(buffer[i:])
                    break

    async def process_packet(self, buffer):
        try:
            from NASAPacket import NASAPacket, AddressClassEnum
            nasa_packet = NASAPacket()
            nasa_packet.parse(buffer)
            if nasa_packet.packet_source_address_class in (AddressClassEnum.Outdoor, AddressClassEnum.Indoor):
                await self.processor.process_message(nasa_packet)
        except Exception as e:
            _LOGGER.error(f"Fehler beim Verarbeiten des Pakets: {e}")

    async def stop(self):
        self.running = False