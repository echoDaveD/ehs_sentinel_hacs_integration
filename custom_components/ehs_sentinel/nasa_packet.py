from enum import Enum
from .nasa_message import NASAMessage
import binascii
import struct 
import logging

_LOGGER = logging.getLogger(__name__)

class AddressClassEnum(Enum):
    """
    Enum class representing various address classes for NASA packets.
    """

    Outdoor = 0x10
    HTU = 0x11
    Indoor = 0x20
    ERV = 0x30
    Diffuser = 0x35
    MCU = 0x38
    RMC = 0x40
    WiredRemote = 0x50
    PIM = 0x58
    SIM = 0x59
    Peak = 0x5A
    PowerDivider = 0x5B
    OnOffController = 0x60
    WiFiKit = 0x62
    CentralController = 0x65
    DMS = 0x6A
    JIGTester = 0x80
    BroadcastSelfLayer = 0xB0
    BroadcastControlLayer = 0xB1
    BroadcastSetLayer = 0xB2
    BroadcastCS = 0xB3
    BroadcastControlAndSetLayer = 0xB3
    BroadcastModuleLayer = 0xB4
    BroadcastCSM = 0xB7
    BroadcastLocalLayer = 0xB8
    BroadcastCSML = 0xBF
    Undefined = 0xFF

class PacketType(Enum):
    """
    Enum class representing different types of packets in the EHS-Sentinel system.
    """

    StandBy = 0
    Normal = 1
    Gathering = 2
    Install = 3
    Download = 4

class DataType(Enum):
    """
    Enum representing different types of data operations.
    """

    Undefined = 0
    Read = 1
    Write = 2
    Request = 3
    Notification = 4
    Resposne = 5
    Ack = 6
    Nack = 7

class NASAPacket:
    """
    A class to represent a NASA Packet.
    """

    def __init__(self):
        self._packet_raw: bytearray = None
        self.packet_start: int = None
        self.packet_size: int = None
        self.packet_source_address_class: AddressClassEnum = None
        self.packet_source_channel: int = None
        self.packet_source_address: int = None
        self.packet_dest_address_class: AddressClassEnum = None
        self.packet_dest_channel: int = None
        self.packet_dest_address: int = None
        self.packet_information: int = None
        self.packet_version: int = None
        self.packet_retry_count: int = None
        self.packet_type: PacketType = None
        self.packet_data_type: DataType = None
        self.packet_number: int = None
        self.packet_capacity: int = None
        self.packet_messages: list[NASAMessage] = None
        self.packet_crc16: int = None
        self.packet_end: int = None

    def parse(self, packet: bytearray):
        self._packet_raw = packet
        if len(packet) < 14:
            raise ValueError("Data too short to be a valid NASAPacket")
        
        crc_checkusm=binascii.crc_hqx(bytearray(packet[3:-3]), 0)

        self.packet_start = packet[0]
        self.packet_size = ((packet[1] << 8) | packet[2])

        if self.packet_size+2 != len(packet):
            _LOGGER.debug(f"length not correct {self.packet_size+2} -> {len(packet)}")
            _LOGGER.debug(f"{packet.hex()}")
            _LOGGER.debug(f"{hex(packet[self.packet_size+1])}")

        try:
            self.packet_source_address_class = AddressClassEnum(packet[3])
        except ValueError as e:
            raise ValueError(f"Source Adress Class out of enum {packet[3]}")
        
        self.packet_source_channel = packet[4]
        self.packet_source_address = packet[5]

        try:
            self.packet_dest_address_class = AddressClassEnum(packet[6])
        except ValueError as e:
            raise ValueError(f"Destination Adress Class out of enum {packet[6]}")
        
        self.packet_dest_channel = packet[7]
        self.packet_dest_address = packet[8]
        self.packet_information = (int(packet[9]) & 128) >> 7 == 1
        self.packet_version = (int(packet[9]) & 96) >> 5
        self.packet_retry_count = (int(packet[9]) & 24) >> 3
        self.packet_type = PacketType((int(packet[10]) & 240) >> 4)
        self.packet_data_type = DataType(int(packet[10]) & 15)
        self.packet_number = packet[11]
        self.packet_capacity = packet[12]
        self.packet_crc16 = ((packet[-3] << 8) | packet[-2]) # + 2
        self.packet_end = packet[-1]
        self.packet_messages = self._extract_messages(0, self.packet_capacity, packet[13:-3], [])

        if crc_checkusm != self.packet_crc16:
            raise ValueError(f"Checksum for package could not be validated. Calculated: {crc_checkusm} in packet: {self.packet_crc16}: packet:{self}")

    def _extract_messages(self, depth: int, capacity: int, msg_rest: bytearray, return_list: list):
        if depth > capacity or len(msg_rest) <= 2:
            return return_list
        
        message_number = (msg_rest[0] << 8) | msg_rest[1]
        message_type = (message_number & 1536) >> 9

        if message_type == 0:
            payload_size = 1
        elif message_type == 1:
            payload_size = 2
        elif message_type == 2:
            payload_size = 4
        elif message_type == 3:
            payload_size = len(msg_rest)  
            if capacity != 1:
                raise ValueError("Message with structure type must have capacity of 1.")
        else:
            raise ValueError(f"Mssage type unknown: {message_type}")
        
        payload = msg_rest[2:2 + payload_size]
        if len(payload) > 255:
            raise ValueError(f"Payload for Submessage {hex(message_number)} too large at index {depth}: {len(payload)} bytes.")
        
        return_list.append(NASAMessage(packet_message=message_number, packet_message_type=message_type, packet_payload=payload))
        return self._extract_messages(depth+1, capacity, msg_rest[2 + payload_size:], return_list)

    def __str__(self):
        text =  f"NASAPacket(\n"
        text += f"    start={self.packet_start} ({hex(self.packet_start)}),\n"
        text += f"    size={self.packet_size} ({hex(self.packet_size)}),\n"
        text += f"    source_address_class={self.packet_source_address_class} ({hex(self.packet_source_address_class.value)}),\n"
        text += f"    source_channel={self.packet_source_channel} ({hex(self.packet_source_channel)}),\n"
        text += f"    source_address={self.packet_source_address} ({hex(self.packet_source_address)}),\n"
        text += f"    dest_address_class={self.packet_dest_address_class} ({hex(self.packet_dest_address_class.value)}),\n"
        text += f"    dest_channel={self.packet_dest_channel} ({hex(self.packet_dest_channel)}),\n"
        text += f"    dest_address={self.packet_dest_address} ({hex(self.packet_dest_address)}),\n"
        text += f"    information={self.packet_information},\n"
        text += f"    version={self.packet_version} ({hex(self.packet_version)}),\n"
        text += f"    retry_count={self.packet_retry_count} ({hex(self.packet_retry_count)}),\n"
        text += f"    type={self.packet_type} ({hex(self.packet_type.value)}),\n"
        text += f"    data_type={self.packet_data_type} ({hex(self.packet_data_type.value)}),\n"
        text += f"    number={self.packet_number} ({hex(self.packet_number)}),\n"
        text += f"    capacity={self.packet_capacity} ({hex(self.packet_capacity)}),\n"
        text += f"    messages=[\n"
        for msg in self.packet_messages:
            lines = f"{msg}".splitlines()
            text += f"        {lines[0]}\n"
            for line in lines[1:-1]:
                text += f"            {line}\n"
            text += f"        {lines[-1]}\n"
        text +=  "    ],\n"
        text += f"    crc16={self.packet_crc16} ({hex(self.packet_crc16)}),\n"
        text += f"    end={self.packet_end} ({hex(self.packet_end)})\n"
        text += f")"
        return text

    def __repr__(self):
        return self.__str__()
    
    # Setter methods
    def set_packet_source_address_class(self, value: AddressClassEnum):
        self.packet_source_address_class = value

    def set_packet_source_channel(self, value: int):
        self.packet_source_channel = value

    def set_packet_source_address(self, value: int):
        self.packet_source_address = value

    def set_packet_dest_address_class(self, value: AddressClassEnum):
        self.packet_dest_address_class = value

    def set_packet_dest_channel(self, value: int):
        self.packet_dest_channel = value

    def set_packet_dest_address(self, value: int):
        self.packet_dest_address = value

    def set_packet_information(self, value: bool):
        self.packet_information = value

    def set_packet_version(self, value: int):
        self.packet_version = value

    def set_packet_retry_count(self, value: int):
        self.packet_retry_count = value

    def set_packet_type(self, value: PacketType):
        self.packet_type = value

    def set_packet_data_type(self, value: DataType):
        self.packet_data_type = value

    def set_packet_number(self, value: int):
        self.packet_number = value

    def set_packet_messages(self, value: list[NASAMessage]):
        self.packet_messages = value

    def to_raw(self) -> bytearray:
        self.packet_start = 50
        self.packet_end = 52
        

        packet = bytearray()
        packet.append(int(self.packet_start))
        packet.append(0)
        packet.append(0)
        packet.append(self.packet_source_address_class.value)
        packet.append(self.packet_source_channel)
        packet.append(self.packet_source_address)
        packet.append(self.packet_dest_address_class.value)
        packet.append(self.packet_dest_channel)
        packet.append(self.packet_dest_address)
        packet.append((self.packet_information << 7) | (self.packet_version << 5) | (self.packet_retry_count << 3))
        packet.append((self.packet_type.value << 4) | self.packet_data_type.value)
        packet.append(self.packet_number)
        packet.append(len(self.packet_messages))
        
        # Add messages to the packet
        for msg in self.packet_messages:
            for msg_pack in msg.to_raw():
                packet.append(msg_pack)

        self.packet_capacity = len(self.packet_messages)
        self.packet_size = len(packet)+2+2
        packet[1] = (self.packet_size >> 8) & 0xFF
        packet[2] = self.packet_size & 0xFF
        self.packet_crc16=binascii.crc_hqx(packet[3:], 0)
        final_packet = struct.pack(">BH", packet[0], len(packet[1:])+2) + packet[3:] + struct.pack(">HB", self.packet_crc16, 0x34)
        return final_packet

# Example usage:
#string_bytearray =  ['0x32', '0x0', '0x11', '0x20', '0x0', '0x0', '0xb3', '0x0', '0xff', '0xc0', '0x14', '0x3a', '0x1', '0x40', '0x46', '0x1', '0xbd', '0x65', '0x34']
#packet = NASAPacket()
#packet.parse(bytearray((int(b, 16) for b in string_bytearray)))
#print(packet)