"""Tests runnable without importing Home Assistant."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

import yaml

DECODER_PATH = Path(__file__).resolve().parents[1] / "custom_components/ehs_sentinel/structure_decoder.py"
SPEC = importlib.util.spec_from_file_location("structure_decoder", DECODER_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
decode_structure = MODULE.decode_structure

FINGERPRINT = {
    "min_length": 5,
    "fields": {
        "field_a": {"offset": 0, "length": 2, "encoding": "uint", "endian": "big"},
        "field_b": {"offset": 2, "length": 2, "encoding": "uint", "endian": "big"},
        "field_c": {"offset": 4, "length": 1, "encoding": "uint"},
    },
    "format": "{field_a}/{field_b}/0x{field_c:02X}",
}
MODEL = {
    "min_length": 2,
    "fields": {
        "type_id": {"offset": 0, "length": 1, "encoding": "uint"},
        "model_name": {"offset": 1, "encoding": "ascii", "terminator": "00"},
    },
    "format": "{model_name} (type: 0x{type_id:02X})",
}


class StructureDecoderTests(unittest.TestCase):
    def test_without_schema_preserves_all_bytes(self):
        for payload in (bytes.fromhex("0008000efe"), b"\x09EHS MONO\x00", b"\x00\xff"):
            with self.subTest(payload=payload):
                self.assertEqual(decode_structure(payload), payload.hex())

    def test_empty_payload(self):
        self.assertIsNone(decode_structure(b""))
        self.assertIsNone(decode_structure(b"", FINGERPRINT))

    def test_fingerprint(self):
        self.assertEqual(decode_structure(bytes.fromhex("0008000efe"), FINGERPRINT), "8/14/0xFE")

    def test_models(self):
        for type_id, name in ((9, "EHS MONO"), (17, "EHS MONO LOWTEMP")):
            payload = bytes([type_id]) + name.encode("ascii") + b"\x00"
            self.assertEqual(decode_structure(payload, MODEL), f"{name} (type: 0x{type_id:02X})")

    def test_invalid_payloads_return_complete_hex(self):
        for payload, schema in ((b"\x00\x01", FINGERPRINT), (b"\x09\xff\x00", MODEL),
                                (b"\x09EHS", MODEL), (b"\x09\x00", MODEL)):
            with self.subTest(payload=payload):
                self.assertEqual(decode_structure(payload, schema), payload.hex())

    def test_integer_endianness_and_hex_remainder(self):
        schema = {
            "fields": {
                "signed": {"length": 2, "encoding": "int", "endian": "little"},
                "rest": {"offset": 2, "encoding": "hex"},
            },
            "format": "{signed}/{rest}",
        }
        self.assertEqual(decode_structure(b"\xfe\xff\x00\x01", schema), "-2/0001")

    def test_bcd_date(self):
        schema = {
            "fields": {"date": {"length": 3, "encoding": "bcd_date"}},
            "format": "({date})",
        }
        self.assertEqual(decode_structure(bytes.fromhex("220706"), schema), "(2022-07-06)")
        self.assertEqual(decode_structure(bytes.fromhex("000000"), schema), "(unknown)")
        for payload in (bytes.fromhex("22fa06"), bytes.fromhex("220231"), bytes.fromhex("2207")):
            with self.subTest(payload=payload):
                self.assertEqual(decode_structure(payload, schema), payload.hex())

    def test_invalid_schemas_and_unsafe_templates_fall_back(self):
        for schema in (False, {}, {"fields": [], "format": ""},
                       {"fields": {"value": {"encoding": "unknown"}}, "format": "{value}"}):
            with self.subTest(schema=schema):
                self.assertEqual(decode_structure(b"abc", schema), "616263")
        for template in ("{value.__class__}", "{value[0]}", "{value!r}", "{missing}",
                         "{value:{value}}", "{value:999999999d}", "{"):
            schema = {"fields": {"value": {"encoding": "hex"}}, "format": template}
            with self.subTest(template=template):
                self.assertEqual(decode_structure(b"abc", schema), "616263")

    def test_invalid_field_options_fall_back(self):
        for field in ({"offset": -1}, {"offset": True}, {"length": "2"},
                      {"length": 10}, {"offset": 4}, {"length": 0, "encoding": "uint"},
                      {"encoding": "ascii", "terminator": "zz"},
                      {"encoding": "ascii", "terminator": ""},
                      {"encoding": "uint", "endian": "middle"}, {"unknown": 1}):
            schema = {"fields": {"value": field}, "format": "{value}"}
            with self.subTest(field=field):
                self.assertEqual(decode_structure(b"abc", schema), "616263")


class RepositoryDecoderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = DECODER_PATH.parent
        self.repository = yaml.safe_load((root / "data/nasa_repository.yml").read_text(encoding="utf-8"))
        tree = ast.parse((root / "coordinator.py").read_text(encoding="utf-8"))
        coordinator = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                           and node.name == "EHSSentinelCoordinator")
        method = next(node for node in coordinator.body if isinstance(node, ast.AsyncFunctionDef)
                      and node.name == "determine_value")
        namespace = {"decode_structure": decode_structure}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "coordinator.py", "exec"), namespace)
        self.determine_value = namespace["determine_value"]

    async def test_configured_repository_messages(self):
        cases = (
            ("NASA_OUTDOOR_MODELINFORMATION", "0008000efe", "8/14/0xFE"),
            ("NASA_PRODUCT_MODEL_NAME", "09454853204d4f4e4f00", "EHS MONO (type: 0x09)"),
            ("NASA_PRODUCT_MODEL_NAME", "11454853204d4f4e4f204c4f5754454d5000", "EHS MONO LOWTEMP (type: 0x11)"),
            ("NASA_MICOM_CODE_OUTDOOR", "9102450a220706000000", "DB91-02450A (2022-07-06)"),
            ("NASA_MICOM_CODE_INDOOR", "9102103b231214090909", "DB91-02103B (2023-12-14)"),
            ("NASA_EEPROM_CODE", "8207085a220622000000", "DB82-07085A (2022-06-22)"),
            ("NASA_OUTDOOR_SUBMICOM", "9102381a000000001000010000",
             "DB91-02381A (unknown) [bootloader=010000]"),
            ("NASA_OUTDOOR_SUBMICOM", "9102381a000000001000",
             "DB91-02381A (unknown) [bootloader=]"),
        )
        coordinator = SimpleNamespace(nasa_repo=self.repository)
        for name, payload_hex, expected in cases:
            with self.subTest(name=name, payload=payload_hex):
                actual = await self.determine_value(coordinator, bytes.fromhex(payload_hex), name, 3)
                self.assertEqual(actual, expected)
                self.assertEqual(await self.determine_value(coordinator, b"\x01", name, 3), "01")

    async def test_no_decode_uses_hex_and_ignores_address(self):
        repository = {
            "plain": {"address": "0x061A"},
            "arbitrary": {"address": "0xFFFF", "decode": self.repository["NASA_OUTDOOR_MODELINFORMATION"]["decode"]},
        }
        coordinator = SimpleNamespace(nasa_repo=repository)
        self.assertEqual(await self.determine_value(coordinator, b"\x09EHS\x00", "plain", 3), "0945485300")
        self.assertEqual(await self.determine_value(coordinator, bytes.fromhex("0008000efe"), "arbitrary", 3), "8/14/0xFE")

    def test_micom_entries_are_selected_by_source_class(self):
        root = DECODER_PATH.parent
        tree = ast.parse((root / "message_processor.py").read_text(encoding="utf-8"))
        processor = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                         and node.name == "MessageProcessor")
        method = next(node for node in processor.body if isinstance(node, ast.FunctionDef)
                      and node.name == "search_nasa_table")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "message_processor.py", "exec"), namespace)
        instance = SimpleNamespace(coordinator=SimpleNamespace(nasa_repo=self.repository))
        self.assertEqual(namespace["search_nasa_table"](instance, "0x0608", "Outdoor"), "NASA_MICOM_CODE_OUTDOOR")
        self.assertEqual(namespace["search_nasa_table"](instance, "0x0608", "Indoor"), "NASA_MICOM_CODE_INDOOR")

    def test_read_request_routes_come_from_repository_metadata(self):
        from enum import IntEnum

        class AddressClasses(IntEnum):
            Indoor = 0x20
            Outdoor = 0x10
            JIGTester = 0x80
            BroadcastSetLayer = 0xB2
            BroadcastSelfLayer = 0xB0

        root = DECODER_PATH.parent
        tree = ast.parse((root / "message_producer.py").read_text(encoding="utf-8"))
        producer = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                        and node.name == "MessageProducer")
        methods = [
            node for node in producer.body
            if isinstance(node, ast.FunctionDef)
            and node.name in ("_get_read_request_route", "_get_read_request_batches")
        ]
        namespace = {"AddressClassEnum": AddressClasses}
        exec(compile(ast.Module(body=methods, type_ignores=[]), "message_producer.py", "exec"), namespace)

        instance = SimpleNamespace(
            coordinator=SimpleNamespace(
                nasa_repo=self.repository,
                indoor_address={"class": AddressClasses.Indoor, "channel": 7, "address": 32},
                outdoor_address={"class": AddressClasses.Outdoor, "channel": 9, "address": 16},
            ),
            _CHUNKSIZE=10,
        )
        get_route = namespace["_get_read_request_route"]
        model_route = get_route(instance, "NASA_PRODUCT_MODEL_NAME")
        chiller_route = get_route(instance, "ENUM_IN_CHILLLER_SETTING_SILENT_LEVEL")

        self.assertEqual(model_route, (
            AddressClasses.JIGTester, 0, 255,
            AddressClasses.BroadcastSelfLayer, 255, 20,
        ))
        self.assertEqual(chiller_route, (
            AddressClasses.JIGTester, 255, 0,
            AddressClasses.Outdoor, 9, 16,
        ))
        instance._get_read_request_route = lambda message: get_route(instance, message)
        batches = namespace["_get_read_request_batches"](
            instance, ["NASA_PRODUCT_MODEL_NAME", "ENUM_IN_CHILLLER_SETTING_SILENT_LEVEL"]
        )
        self.assertEqual([batch[1] for batch in batches], [
            ["NASA_PRODUCT_MODEL_NAME"],
            ["ENUM_IN_CHILLLER_SETTING_SILENT_LEVEL"],
        ])

    def test_software_versions_log_once_after_all_three_are_stored(self):
        root = DECODER_PATH.parent
        tree = ast.parse((root / "message_processor.py").read_text(encoding="utf-8"))
        processor = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                         and node.name == "MessageProcessor")
        method = next(node for node in processor.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_log_software_versions_if_ready")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "message_processor.py", "exec"), namespace)
        log_entries = []
        warning_entries = []
        instance = SimpleNamespace(
            value_store={},
            _software_versions_logged=False,
            _logger=SimpleNamespace(
                info=lambda *args: log_entries.append(args),
                warning=lambda *args: warning_entries.append(args),
            ),
        )
        log_if_ready = namespace["_log_software_versions_if_ready"]

        response = log_if_ready(instance, force=True)
        self.assertEqual(response["missing"], [
            "NASA_MICOM_CODE_OUTDOOR",
            "NASA_MICOM_CODE_INDOOR",
            "NASA_EEPROM_CODE",
        ])
        self.assertIsNone(response["outdoor_main_micom"])
        self.assertEqual(len(warning_entries), 1)

        instance.value_store["NASA_MICOM_CODE_OUTDOOR"] = {"val": "DB91-02450A (2022-07-06)"}
        log_if_ready(instance)
        instance.value_store["NASA_MICOM_CODE_INDOOR"] = {"val": "DB91-02103B (2023-12-14)"}
        log_if_ready(instance)
        self.assertEqual(log_entries, [])

        instance.value_store["NASA_EEPROM_CODE"] = {"val": "DB82-07085A (2022-06-22)"}
        response = log_if_ready(instance)
        self.assertEqual(len(log_entries), 1)
        self.assertEqual(response, {
            "outdoor_main_micom": "DB91-02450A (2022-07-06)",
            "indoor_main_micom": "DB91-02103B (2023-12-14)",
            "outdoor_eeprom": "DB82-07085A (2022-06-22)",
            "missing": [],
        })

        log_if_ready(instance, force=True)
        self.assertEqual(len(log_entries), 2)
        self.assertIn("DB91-02450A (2022-07-06)", log_entries[0])
        self.assertIn("DB91-02103B (2023-12-14)", log_entries[0])
        self.assertIn("DB82-07085A (2022-06-22)", log_entries[0])

        log_if_ready(instance)
        self.assertEqual(len(log_entries), 2)

    async def test_numeric_and_enum_behavior_is_unchanged(self):
        coordinator = SimpleNamespace(nasa_repo={
            "numeric": {"arithmetic": "value / 10"},
            "enum": {"type": "ENUM", "enum": {1: "ON"}},
        })
        self.assertEqual(await self.determine_value(coordinator, b"\x00\xd7", "numeric", 1), 21.5)
        self.assertEqual(await self.determine_value(coordinator, b"\xff\x9c", "numeric", 1), -10)
        self.assertEqual(await self.determine_value(coordinator, b"\x01", "enum", 0), "ON")


if __name__ == "__main__":
    unittest.main()