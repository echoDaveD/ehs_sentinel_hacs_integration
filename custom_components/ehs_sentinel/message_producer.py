import logging
import asyncio

from .nasa_message import NASAMessage
from .nasa_packet import NASAPacket, AddressClassEnum, PacketType, DataType

_LOGGER = logging.getLogger(__name__)

class MessageProducer:
    """Erzeugt und sendet Nachrichten an das EHS Sentinel System."""
    _CHUNKSIZE = 10

    def __init__(self, hass, coordinator):
        self.hass = hass
        self.coordinator = coordinator
        self.writer = None

    def set_writer(self, writer):
        """Setzt den Writer für die serielle Kommunikation."""
        self.writer = writer

    async def read_request(self, list_of_messages: list):
        chunks = [list_of_messages[i:i + self._CHUNKSIZE] for i in range(0, len(list_of_messages), self._CHUNKSIZE)]
        for chunk in chunks:
            await asyncio.sleep(0.5)
            nasa_packet = self._build_default_read_packet()
            nasa_packet.set_packet_messages([self._build_message(x) for x in chunk])
            await self._write_packet_to_serial(nasa_packet)

    async def write_request(self, message: str, value: str | int, read_request_after=False):

        max_retries = 3
        message = message.strip()
        value = self._decode_value(message, value.strip())
        nasamessage = self._build_message(message, value)
        nasa_packet = self._build_default_request_packet()
        nasa_packet.set_packet_messages([nasamessage])
        nasa_packet.to_raw()

        for attempt in range(max_retries):
            _LOGGER.info(f"Write request for {message} with value: {value}")
            _LOGGER.debug(f"Sending NASA packet: {nasa_packet}")

            await self._write_packet_to_serial(nasa_packet)

            if read_request_after:
                await asyncio.sleep(1)
                await self.read_request([message])

                event = self.coordinator.create_write_confirmation(message)
                try:
                    await asyncio.wait_for(event.wait(), timeout=5)
                    break  # Erfolg, Schleife verlassen
                except asyncio.TimeoutError:
                    _LOGGER.warning(f"No confirmation for {message} after 5s (attempt {attempt+1}/{max_retries})")
                    if attempt == max_retries - 1:
                        _LOGGER.error(f"Write failed for {message} after {max_retries} attempts")
                finally:
                    # Event ggf. aufräumen
                    self.coordinator._write_confirmations.pop(message, None)
                    
            else:
                break

    def _search_nasa_enumkey_for_value(self, message, value):
        if 'type' in self.coordinator.nasa_repo[message] and self.coordinator.nasa_repo[message]['type'] == 'ENUM':
            for key, val in self.coordinator.nasa_repo[message]['enum'].items():
                if val == value:
                    return key
                
        return None
    
    def is_number(self, s):
        return s.replace('+','',1).replace('-','',1).replace('.','',1).isdigit()

    def _decode_value(self, message, value) -> int:  
        enumval = self._search_nasa_enumkey_for_value(message, value)
        if enumval is None:
            if self.is_number(value):
                try:
                    value = int(value)
                except ValueError as e:
                    value = float(value)

                if 'reverse-arithmetic' in self.coordinator.nasa_repo[message]:
                    arithmetic = self.coordinator.nasa_repo[message]['reverse-arithmetic']
                else: 
                    arithmetic = ''
                if len(arithmetic) > 0:
                    try:
                        return int(eval(arithmetic))
                    except Exception as e:
                        _LOGGER.warning(f"Arithmetic Function couldn't been applied for Message {message}, using raw value: reverse-arithmetic = {arithmetic} {e} {value}")
                        return value
                else:
                    value = int(value)
        else:
            value = int(enumval)

        return value

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
        elif tmpmsg.packet_message_type == 3:
            value_raw = value.to_bytes(1, byteorder='big', signed=True)
        else:
            raise Exception(message=f"Unknown Type for {message} type: {tmpmsg.packet_message_type}")
        
        tmpmsg.set_packet_payload_raw(value_raw)
        return tmpmsg

    def _extract_address(self, messagename) -> int:
        return int(self.coordinator.nasa_repo[messagename]['address'], 16)

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

    def _build_default_request_packet(self) -> NASAPacket:
        nasa_msg = NASAPacket()
        nasa_msg.set_packet_source_address_class(AddressClassEnum.JIGTester)
        nasa_msg.set_packet_source_channel(0)
        nasa_msg.set_packet_source_address(255)
        nasa_msg.set_packet_dest_address_class(AddressClassEnum.Indoor)
        nasa_msg.set_packet_dest_channel(0)
        nasa_msg.set_packet_dest_address(self.coordinator.indoor_address)
        nasa_msg.set_packet_information(True)
        nasa_msg.set_packet_version(2)
        nasa_msg.set_packet_retry_count(0)
        nasa_msg.set_packet_type(PacketType.Normal)
        nasa_msg.set_packet_data_type(DataType.Request)
        nasa_msg.set_packet_number(166)
        return nasa_msg

    async def _write_packet_to_serial(self, packet: NASAPacket):
        final_packet = packet.to_raw()
        self.writer.write(final_packet)
        await self.writer.drain()