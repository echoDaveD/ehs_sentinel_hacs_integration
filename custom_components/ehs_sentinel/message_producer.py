import logging
import asyncio
import json
import os
import yaml

from .nasa_Message import NASAMessage
from NASAPacket import NASAPacket, AddressClassEnum, PacketType, DataType


class MessageProducer:
    """Erzeugt und sendet Nachrichten an das EHS Sentinel System."""
    _instance = None
    _CHUNKSIZE = 10
    writer = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(MessageProducer, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, writer):
        if self._initialized:
            return
        self._initialized = True
        self.writer = writer
        self.config = EHSConfig()

    async def read_request(self, list_of_messages: list):
        chunks = [list_of_messages[i:i + self._CHUNKSIZE] for i in range(0, len(list_of_messages), self._CHUNKSIZE)]
        for chunk in chunks:
            await asyncio.sleep(0.5)
            nasa_packet = self._build_default_read_packet()
            nasa_packet.set_packet_messages([self._build_message(x) for x in chunk])
            await self._write_packet_to_serial(nasa_packet)

    def _build_message(self, message, value=0) -> NASAMessage:
        tmpmsg = NASAMessage()
        tmpmsg.set_packet_message(self._extract_address(message))
        if value is None:
            value = 0
        if tmpmsg.packet_message_type == 0:
            value_raw = value.to_bytes(1, byteorder='big', signed=True)
        elif tmpmsg.packet_message_type == 1:
            value_raw = value.to_bytes(2, byteorder='big', signed=True)
        elif tmpmsg.packet_message_type == 2:
            value_raw = value.to_bytes(4, byteorder='big', signed=True)
        else:
            value_raw = value.to_bytes(1, byteorder='big', signed=True)
        tmpmsg.set_packet_payload_raw(value_raw)
        return tmpmsg

    def _extract_address(self, messagename) -> int:
        return int(self.config.NASA_REPO[messagename]['address'], 16)

    def _build_default_read_packet(self) -> NASAPacket:
        nasa_msg = NASAPacket()
        nasa_msg.set_packet_source_address_class(AddressClassEnum.JIGTester)
        nasa_msg.set_packet_source_channel(255)
        nasa_msg.set_packet_source_address(0)
        nasa_msg.set_packet_dest_address_class(AddressClassEnum.BroadcastSetLayer)
        nasa_msg.set_packet_dest_channel(0)
        nasa_msg.set_packet_dest_address(32)
        nasa_msg.set_packet_information(True)
        nasa_msg.set_packet_version(2)
        nasa_msg.set_packet_retry_count(0)
        nasa_msg.set_packet_type(PacketType.Normal)
        nasa_msg.set_packet_data_type(DataType.Read)
        nasa_msg.set_packet_number(166)
        return nasa_msg

    async def _write_packet_to_serial(self, packet: NASAPacket):
        final_packet = packet.to_raw()
        self.writer.write(final_packet)
        await self.writer.drain()