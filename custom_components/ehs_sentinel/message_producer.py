import logging
import asyncio

from .nasa_message import NASAMessage
from .nasa_packet import NASAPacket, AddressClassEnum, PacketType, DataType

_LOGGER = logging.getLogger(__name__)

CONFIRMATION_REQUIRED_PREFIXES = (
    "VAR_OUT_",
    "ENUM_OUT_",
)


def requires_confirmation(fsv_id: str) -> bool:
    return fsv_id.startswith(CONFIRMATION_REQUIRED_PREFIXES)


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

        if (
            self.coordinator.indoor_address is None
            or self.coordinator.outdoor_address is None
        ):
            _LOGGER.error(
                "Cannot send read request: Indoor or Outdoor Unit Address is not set. "
                "Wait till auto-detection is complete."
            )
            return False

        max_retries = 3
        chunks = [
            list_of_messages[i : i + self._CHUNKSIZE]
            for i in range(0, len(list_of_messages), self._CHUNKSIZE)
        ]

        for chunk in chunks:
            messages = [self._build_message(x) for x in chunk]
            nasa_packet = self._build_default_read_packet()
            nasa_packet.set_packet_messages(messages)

            if "ENUM_IN_CHILLLER_SETTING_SILENT_LEVEL" in list_of_messages:
                nasa_packet.set_packet_dest_address_class(
                    AddressClassEnum(self.coordinator.outdoor_address["class"])
                )
                nasa_packet.set_packet_dest_channel(
                    self.coordinator.outdoor_address["channel"]
                )
                nasa_packet.set_packet_dest_address(
                    self.coordinator.outdoor_address["address"]
                )

            await asyncio.sleep(0.5)

            # Only confirmations that actually make sense
            confirmable_messages = [
                msg for msg in chunk if requires_confirmation(msg)
            ]

            events = (
                [
                    self.coordinator.create_read_confirmation(msg)
                    for msg in confirmable_messages
                ]
                if retry__mode and confirmable_messages
                else []
            )

            BASE_TIMEOUT = 6
            BACKOFF_FACTOR = 1.5

            tasks = [
                asyncio.create_task(
                    asyncio.wait_for(event.wait(), timeout=BASE_TIMEOUT),
                    name=f"EHSSentinelCoordinator.MessageProducer.read_request.{i}",
                )
                for i, event in enumerate(events)
            ]

            try:
                for attempt in range(max_retries):
                    await self._write_packet_to_serial(nasa_packet)

                    if not events:
                        break  # nothing to wait for

                    timeout = BASE_TIMEOUT * (BACKOFF_FACTOR ** attempt)

                    done, pending = await asyncio.wait(
                        tasks,
                        timeout=timeout,
                        return_when=asyncio.ALL_COMPLETED,
                    )

                    if len(done) < len(tasks):
                        log = (
                            _LOGGER.debug
                            if attempt < max_retries - 1
                            else _LOGGER.warning
                        )
                        log(
                            f"No confirmation for {confirmable_messages} "
                            f"after {timeout:.1f}s (attempt {attempt+1}/{max_retries})"
                        )

                        if attempt == max_retries - 1:
                            _LOGGER.error(
                                f"Read failed for {confirmable_messages} "
                                f"after {max_retries} attempts"
                            )
                            if self.coordinator.extended_logging:
                                _LOGGER.info(f"Failed NasaPacket: {nasa_packet}")
                            return False
                    else:
                        break  # success
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

                for message in confirmable_messages:
                    self.coordinator._read_confirmations.pop(message, None)

        return True

    async def write_request(
        self,
        message: str | list,
        value: str | int | list,
        read_request_after=False,
        source_address_class=None,
        source_channel=None,
        source_address=None,
        dest_address_class=None,
        dest_channel=None,
        dest_address=None,
        packet_type=None,
        data_type=None,
    ):

        if not isinstance(message, list):
            message = [message]

        if not isinstance(value, list):
            value = [value]

        if (
            self.coordinator.indoor_address is None
            or self.coordinator.outdoor_address is None
        ):
            _LOGGER.error(
                "Cannot send write request: Indoor or Outdoor Unit Address is not set. "
                "Wait till auto-detection is complete."
            )
            return False

        message = [tmp.strip() for tmp in message]
        value = [
            self._decode_value(tmp_msg, tmp_value)
            for tmp_msg, tmp_value in dict(zip(message, value)).items()
        ]

        max_retries = 3
        nasamessages = [
            self._build_message(tmp_message, tmp_value)
            for tmp_message, tmp_value in zip(message, value)
        ]

        nasa_packet = self._build_default_request_packet()
        nasa_packet.set_packet_messages(nasamessages)

        if read_request_after:
            events = []
            determinated_values = []
            for nm, msgname in zip(nasamessages, message):
                try:
                    det_val = await self.coordinator.determine_value(
                        nm.packet_payload, msgname, nm.packet_message_type
                    )
                except Exception:
                    det_val = None
                determinated_values.append(det_val)
                events.append(
                    self.coordinator.create_write_confirmation(msgname, det_val)
                )

        for attempt in range(max_retries):
            await self._write_packet_to_serial(nasa_packet)

            if not read_request_after:
                break

            await asyncio.sleep(1)
            await self.read_request(message)

            tasks = [
                asyncio.create_task(asyncio.wait_for(ev.wait(), timeout=3))
                for ev in events
            ]

            try:
                done, pending = await asyncio.wait(
                    tasks, timeout=3, return_when=asyncio.ALL_COMPLETED
                )
                if len(done) < len(tasks):
                    _LOGGER.warning(
                        f"No confirmation for {'/'.join(message)} "
                        f"after 3s (attempt {attempt+1}/{max_retries})"
                    )
                    if attempt == max_retries - 1:
                        _LOGGER.error(
                            f"Write failed for {'/'.join(message)} "
                            f"after {max_retries} attempts"
                        )
                        return False
                else:
                    break
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        for msgname in message:
            self.coordinator._write_confirmations.pop(msgname, None)

        return True

    def _search_nasa_enumkey_for_value(self, message, value):
        if (
            "type" in self.coordinator.nasa_repo[message]
            and self.coordinator.nasa_repo[message]["type"] == "ENUM"
        ):
            for key, val in self.coordinator.nasa_repo[message]["enum"].items():
                if val == value:
                    return key
        return None

    def is_number(self, s):
        return s.replace("+", "", 1).replace("-", "", 1).replace(".", "", 1).isdigit()

    def _decode_value(self, message, value) -> int:
        enumval = self._search_nasa_enumkey_for_value(message, value)
        if enumval is None:
            if self.is_number(value):
                value = int(float(value))
        else:
            value = int(enumval)
        return value

    def _build_message(self, message, value=0) -> NASAMessage:
        tmpmsg = NASAMessage()
        tmpmsg.set_packet_message(self._extract_address(message))
        value = 0 if value is None else value

        size = {0: 1, 1: 2, 2: 4, 3: 1}.get(tmpmsg.packet_message_type)
        if size is None:
            raise Exception(
                f"Unknown Type for {message} type: {tmpmsg.packet_message_type}"
            )

        tmpmsg.set_packet_payload_raw(
            value.to_bytes(size, byteorder="big", signed=True)
        )
        return tmpmsg

    def _extract_address(self, messagename) -> int:
        return int(self.coordinator.nasa_repo[messagename]["address"], 16)

    def _build_default_read_packet(self) -> NASAPacket:
        nasa_msg = NASAPacket()
        nasa_msg.set_packet_source_address_class(AddressClassEnum.JIGTester)
        nasa_msg.set_packet_source_channel(255)
        nasa_msg.set_packet_source_address(0)
        nasa_msg.set_packet_dest_address_class(AddressClassEnum.BroadcastSetLayer)
        nasa_msg.set_packet_dest_channel(self.coordinator.indoor_address["channel"])
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
        nasa_msg.set_packet_dest_address_class(
            AddressClassEnum(self.coordinator.indoor_address["class"])
        )
        nasa_msg.set_packet_dest_channel(self.coordinator.indoor_address["channel"])
        nasa_msg.set_packet_dest_address(self.coordinator.indoor_address["address"])
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
