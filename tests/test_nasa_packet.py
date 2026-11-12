"""NASA packet validation tests without Home Assistant imports."""

import ast
import binascii
from enum import Enum
import logging
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1] / "custom_components/ehs_sentinel"
namespace = {"Enum": Enum, "binascii": binascii, "struct": struct, "logging": logging, "DOMAIN": "ehs_sentinel"}
for filename in ("nasa_message.py", "nasa_packet.py"):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / filename), "exec"), namespace)
NASAPacket = namespace["NASAPacket"]


def make_frame(messages, capacity=None, source=0x10, destination=0x20):
    frame = bytearray([0x32, 0, 0, source, 0, 0, destination, 0, 0, 0x80, 0x14, 1,
                       len(messages) if capacity is None else capacity])
    for message in messages:
        frame.extend(message)
    frame[1:3] = (len(frame) + 1).to_bytes(2, "big")
    frame.extend(binascii.crc_hqx(frame[3:], 0).to_bytes(2, "big"))
    frame.append(0x34)
    return frame


class NASAPacketTests(unittest.TestCase):
    def test_valid_packet_without_messages(self):
        packet = NASAPacket()

        packet.parse(make_frame([]))

        self.assertEqual(packet.packet_capacity, 0)
        self.assertEqual(packet.packet_messages, [])

    def test_empty_structure_payload_is_valid(self):
        packet = NASAPacket()

        packet.parse(make_frame([bytes.fromhex("8600")]))

        self.assertEqual(len(packet.packet_messages), 1)
        self.assertEqual(packet.packet_messages[0].packet_payload, b"")

    def test_valid_packet_addresses_and_payload(self):
        packet = NASAPacket()

        packet.parse(make_frame([bytes.fromhex("800001")]))

        self.assertEqual(packet.packet_source_address_class, namespace["AddressClassEnum"].Outdoor)
        self.assertEqual(packet.packet_dest_address_class, namespace["AddressClassEnum"].Indoor)
        self.assertEqual(packet.packet_capacity, 1)
        self.assertEqual(packet.packet_messages[0].packet_message, 0x8000)
        self.assertEqual(packet.packet_messages[0].packet_payload, b"\x01")

    def test_numeric_message_widths_and_roundtrip(self):
        frame = make_frame([bytes.fromhex("800001"), bytes.fromhex("8200ff9c"),
                            bytes.fromhex("840000010203")])
        packet = NASAPacket()

        packet.parse(frame)

        self.assertEqual([message.packet_message_type for message in packet.packet_messages], [0, 1, 2])
        self.assertEqual([message.packet_payload for message in packet.packet_messages],
                         [b"\x01", bytes.fromhex("ff9c"), bytes.fromhex("00010203")])
        self.assertEqual(packet.to_raw(), bytes(frame))

    def test_structure_payload_roundtrip(self):
        frame = make_frame([bytes.fromhex("86000945485300")])
        packet = NASAPacket()

        packet.parse(frame)

        self.assertEqual(packet.packet_messages[0].packet_payload, b"\x09EHS\x00")
        self.assertEqual(packet.to_raw(), bytes(frame))

    def test_bad_checksum_is_rejected(self):
        frame = make_frame([bytes.fromhex("800001")])
        frame[15] ^= 1
        with self.assertRaisesRegex(ValueError, "Checksum"):
            NASAPacket().parse(frame)

    def test_short_packet_is_rejected(self):
        for length in (0, 1, 13):
            with self.subTest(length=length), self.assertRaises(ValueError):
                NASAPacket().parse(bytearray(length))

    def test_declared_length_mismatch_is_rejected(self):
        frame = make_frame([bytes.fromhex("800001")])
        frame[1:3] = (len(frame) - 3).to_bytes(2, "big")
        with self.assertRaises(ValueError):
            NASAPacket().parse(frame)

    def test_invalid_frame_markers_are_rejected(self):
        for index, marker in ((0, 0x31), (-1, 0x35)):
            with self.subTest(index=index):
                frame = make_frame([bytes.fromhex("800001")])
                frame[index] = marker
                with self.assertRaises(ValueError):
                    NASAPacket().parse(frame)

    def test_truncated_numeric_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            NASAPacket().parse(make_frame([bytes.fromhex("840001")]))

    def test_message_count_mismatch_is_rejected(self):
        for capacity in (0, 1, 3):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                NASAPacket().parse(make_frame([bytes.fromhex("800001"), bytes.fromhex("800102")], capacity))

    def test_unknown_address_classes_are_rejected(self):
        for source, destination in ((0x99, 0x20), (0x10, 0x99)):
            with self.subTest(source=source, destination=destination), self.assertRaises(ValueError):
                NASAPacket().parse(make_frame([bytes.fromhex("800001")], source=source, destination=destination))

    def test_structure_message_cannot_share_packet(self):
        with self.assertRaises(ValueError):
            NASAPacket().parse(make_frame([bytes.fromhex("86000945485300"), bytes.fromhex("800001")]))


if __name__ == "__main__":
    unittest.main()