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

    async def read_request(self, list_of_messages: list, retry__mode=False):
        max_retries = 3
        chunks = [list_of_messages[i:i + self._CHUNKSIZE] for i in range(0, len(list_of_messages), self._CHUNKSIZE)]

        for chunk in chunks:
            messages = [self._build_message(x) for x in chunk]
            nasa_packet = self._build_default_read_packet()
            nasa_packet.set_packet_messages(messages)
            await asyncio.sleep(0.5)

            events = [self.coordinator.create_read_confirmation(message) for message in chunk] if retry__mode else []

            # Wrap every event.wait() in wait_for() so no task runs forever
            tasks = [asyncio.create_task(asyncio.wait_for(event.wait(), timeout=4)) for event in events]

            try:
                for attempt in range(max_retries):
                    await self._write_packet_to_serial(nasa_packet)

                    if retry__mode:
                        done, pending = await asyncio.wait(tasks, timeout=4, return_when=asyncio.ALL_COMPLETED)
                        if len(done) < len(tasks):
                            _LOGGER.warning(f"No confirmation for {chunk} after 4s (attempt {attempt+1}/{max_retries})")
                            if attempt == max_retries - 1:
                                _LOGGER.error(f"Read failed for {chunk} after {max_retries} attempts")
                                return False
                        else:
                            break  # Erfolg
                    else:
                        break  # Kein retry mode → sofort raus
            finally:
                # Garantierter Cleanup — egal ob Erfolg, Fehler oder HA-Shutdown
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

                # Aufräumen der Bestätigungen
                for message in chunk:
                    self.coordinator._read_confirmations.pop(message, None)

    async def write_request(self, message: str | list, 
                            value: str | int | list, 
                            read_request_after=False, 
                            source_address_class=None, 
                            source_channel=None, 
                            source_address=None, 
                            dest_address_class=None, 
                            dest_channel=None, 
                            dest_address=None,
                            packet_type=None,
                            data_type=None):
        
        if not isinstance(message, list):
            message = [message]

        if not isinstance(value, list):
            value = [value]

        message = [tmp.strip() for tmp in message]
        _LOGGER.info(f"dicts {dict(zip(message, value))}")
        value = [self._decode_value(tmp_msg, tmp_value) for tmp_msg, tmp_value in dict(zip(message, value)).items()]
        _LOGGER.info(f"Decoded Values for Messages {message}: {value}")
        max_retries = 3
        
        nasamessages = [self._build_message(tmp_message, tmp_value) for tmp_message, tmp_value in zip(message, value)]
        nasa_packet = self._build_default_request_packet()
        nasa_packet.set_packet_messages(nasamessages)

        # Set optional parameters if provided
        if source_address_class is not None and source_address_class in AddressClassEnum.__members__:
            nasa_packet.set_packet_source_address_class(AddressClassEnum[source_address_class])
        if source_address is not None and 0 <= source_address <= 255:
            nasa_packet.set_packet_source_address(source_address)
        if source_channel is not None and 0 <= source_channel <= 255:
            nasa_packet.set_packet_source_channel(source_channel)
        if dest_address_class is not None and dest_address_class in AddressClassEnum.__members__:
            nasa_packet.set_packet_dest_address_class(AddressClassEnum[dest_address_class])
        if dest_channel is not None and 0 <= dest_channel <= 255:
            nasa_packet.set_packet_dest_channel(dest_channel)
        if dest_address is not None and 0 <= dest_address <= 255:
            nasa_packet.set_packet_dest_address(dest_address)
        if packet_type is not None and packet_type in PacketType.__members__:
            nasa_packet.set_packet_type(PacketType[packet_type])
        if data_type is not None and data_type in DataType.__members__:
            nasa_packet.set_packet_data_type(DataType[data_type])

        # lookup destination address class from nasa_repo when not provided only for first message
        if 'dest_address_class' in self.coordinator.nasa_repo[message[0]] and dest_address_class is None:
            dest_address_class = self.coordinator.nasa_repo[message[0]]['dest_address_class']
            if dest_address_class == 'Outdoor':
                nasa_packet.set_packet_dest_address_class(AddressClassEnum.Outdoor)
                nasa_packet.set_packet_dest_channel(0)
                nasa_packet.set_packet_dest_address(0)

        nasa_packet.to_raw()

        events = []
        determinated_values = []
        
        if read_request_after:
            # for each nasamessage determine expected value and create a confirmation event
            for nm, msgname in zip(nasamessages, message):
                try:
                    det_val = await self.coordinator.determine_value(nm.packet_payload, msgname, nm.packet_message_type)
                except Exception:
                    det_val = None
                determinated_values.append(det_val)
                events.append(self.coordinator.create_write_confirmation(msgname, det_val))

        for attempt in range(max_retries):
            _LOGGER.info(f"Write request for {"/".join(message)} with target value: {determinated_values}")
            _LOGGER.debug(f"Sending NASA packet: {nasa_packet}") #TODO set debug again

            await self._write_packet_to_serial(nasa_packet)
            
            if read_request_after:
                await asyncio.sleep(1)
                await self.read_request(message)

                # create wait tasks for all events (use wait_for wrapped tasks so they time out)
                tasks = [asyncio.create_task(asyncio.wait_for(ev.wait(), timeout=3)) for ev in events]
                
                try:
                    done, pending = await asyncio.wait(tasks, timeout=3, return_when=asyncio.ALL_COMPLETED)
                    if len(done) < len(tasks):
                        _LOGGER.warning(f"No confirmation for {"/".join(message)} after 3s (attempt {attempt+1}/{max_retries})")
                        if attempt == max_retries - 1:
                            _LOGGER.error(f"Write failed for {"/".join(message)} after {max_retries} attempts")
                            # cleanup tasks before returning
                            for t in tasks:
                                if not t.done():
                                    t.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            # cleanup confirmations
                            for msgname in message:
                                self.coordinator._write_confirmations.pop(msgname, None)
                                
                            return False
                        # else retry loop
                    else:
                        # success
                        break
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            else:
                break

        # cleanup write confirmations for all message names
        for msgname in message:
            self.coordinator._write_confirmations.pop(msgname, None)

        return True

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
        nasa_msg.set_packet_dest_channel(self.coordinator.indoor_channel)
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
        nasa_msg.set_packet_dest_channel(self.coordinator.indoor_channel)
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