"""Coordinator unit tests with Home Assistant boundaries replaced by mocks."""

import ast
import asyncio
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
from types import SimpleNamespace
import traceback
import unittest
from unittest.mock import AsyncMock, Mock, patch

import yaml


COORDINATOR_PATH = Path(__file__).resolve().parents[1] / "custom_components/ehs_sentinel/coordinator.py"


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(COORDINATOR_PATH.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.Assign))]
        self.registry = Mock()
        self.namespace = {
            "asyncio": asyncio, "datetime": datetime, "logging": logging, "re": re,
            "yaml": yaml, "traceback": traceback, "DataUpdateCoordinator": object,
            "DeviceInfo": dict, "DOMAIN": "ehs_sentinel", "DEVICE_ID": "test",
            "PLATFORM_SENSOR": "sensor", "PLATFORM_NUMBER": "number",
            "PLATFORM_SWITCH": "switch", "PLATFORM_BINARY_SENSOR": "binary_sensor",
            "PLATFORM_SELECT": "select",
            "er": SimpleNamespace(async_get=Mock(return_value=self.registry),
                                  RegistryEntryDisabler=SimpleNamespace(INTEGRATION="integration")),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(COORDINATOR_PATH), "exec"), self.namespace)
        self.coordinator = object.__new__(self.namespace["EHSSentinelCoordinator"])
        self.coordinator._logger = Mock()
        self.coordinator.hass = Mock()
        self.coordinator.data = {"sensor": {}}
        self.coordinator._data_lock = asyncio.Lock()
        self.coordinator._stats_lock = asyncio.Lock()
        self.coordinator.stats = {"packets_requested": 0}
        self.coordinator._write_confirmations = {}
        self.coordinator._read_confirmations = {}
        self.coordinator._warned_unknown_entities = set()
        self.coordinator._entity_adders = {}
        self.coordinator._entity_factories = {}
        self.coordinator.nasa_repo = {}
        self.coordinator.only_known_entities = True
        self.coordinator.extended_logging = False
        self.coordinator.running = True
        self.coordinator.producer = Mock()
        self.coordinator.processor = Mock(async_reset_cop=AsyncMock())
        self.coordinator._tcp_reader = None
        self.coordinator._tcp_writer = None
        self.coordinator._tcp_read_task = None
        self.coordinator._tcp_write_task = None
        self.coordinator._tcp_task = None
        self.coordinator._diagnostic_task = None
        self.coordinator._tcp_polling_tasks = {}
        self.coordinator._packet_workers = []

    async def test_read_confirmation_only_releases_matching_message(self):
        matching = self.coordinator.create_read_confirmation("matching")
        other = self.coordinator.create_read_confirmation("other")

        self.coordinator.confirm_read("matching")
        self.coordinator.confirm_read("unknown")

        self.assertTrue(matching.is_set())
        self.assertFalse(other.is_set())
        self.assertNotIn("matching", self.coordinator._read_confirmations)

    async def test_write_confirmation_requires_matching_message_and_value(self):
        event = self.coordinator.create_write_confirmation("power", 0)

        self.coordinator.confirm_write("other", 0)
        self.coordinator.confirm_write("power", 1)
        self.assertFalse(event.is_set())
        self.coordinator.confirm_write("power", 0)

        self.assertTrue(event.is_set())
        self.assertNotIn("power", self.coordinator._write_confirmations)

    async def test_confirmation_stores_are_isolated_between_instances(self):
        other = object.__new__(type(self.coordinator))
        other._read_confirmations = {}
        first_event = self.coordinator.create_read_confirmation("power")
        other_event = other.create_read_confirmation("power")

        other.confirm_read("power")

        self.assertFalse(first_event.is_set())
        self.assertTrue(other_event.is_set())

    def test_invalid_or_empty_polling_yaml_returns_empty_mapping(self):
        for raw in (None, "", "null", "[", "[one, two]", "true", "42"):
            with self.subTest(raw=raw):
                self.assertEqual(self.coordinator._parse_polling_yaml(raw), {})

    def test_valid_polling_yaml_preserves_group_lists(self):
        parsed = self.coordinator._parse_polling_yaml("groups:\n  fast: [NASA_POWER, NASA_TEST]\n")
        self.coordinator.polling_yaml = parsed

        self.assertEqual(self.coordinator._polling_yaml_messages(), {"NASA_POWER", "NASA_TEST"})

    def test_polling_interval_units_and_invalid_inputs(self):
        for text, seconds in (("10s", 10), ("2m", 120), ("1h", 3600), (" 3M ", 180)):
            with self.subTest(text=text):
                self.assertEqual(self.coordinator.parse_time_string(text), seconds)
        for text in ("", "-1s", "1.5m", "ten", "10d"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.coordinator.parse_time_string(text)

    async def test_disabled_polling_excludes_group_messages_from_writable_requests(self):
        self.coordinator.polling = False
        self.coordinator.writemode = True
        self.coordinator.polling_yaml = {"groups": {"fsv": ["fsv"]}}
        self.coordinator.nasa_repo = {
            "fsv": {"hass_opts": {"writable": True}},
            "power": {"hass_opts": {"writable": True}},
            "readonly": {"hass_opts": {"writable": False}},
        }
        self.coordinator.producer.read_request = AsyncMock()

        await self.coordinator.request_all_writable_entities()

        self.coordinator.producer.read_request.assert_awaited_once_with(["power"], retry_mode=True)
        self.assertEqual(self.coordinator.stats["packets_requested"], 1)

    def test_unknown_message_is_filtered_with_only_one_warning(self):
        self.coordinator.nasa_repo = {"known": {"hass_opts": {"known": True}}}

        self.assertTrue(self.coordinator.should_process_nasa_name("known"))
        self.assertFalse(self.coordinator.should_process_nasa_name("unknown"))
        self.assertFalse(self.coordinator.should_process_nasa_name("unknown"))
        self.coordinator._logger.warning.assert_called_once()
        self.coordinator.only_known_entities = False
        self.assertTrue(self.coordinator.should_process_nasa_name("unknown"))

    async def test_data_update_merges_metadata_and_retains_entity_reference(self):
        entity = Mock(entity_id="sensor.test")
        self.coordinator.data["sensor"]["test"] = {"_entity": entity, "seen_once": True, "nasa_name": "NASA_TEST"}

        await self.coordinator.update_data_safe({"sensor": {"test": {"value": 2, "nasa_last_seen": "now"}}})

        result = self.coordinator.data["sensor"]["test"]
        self.assertIs(result["_entity"], entity)
        self.assertEqual(result["nasa_name"], "NASA_TEST")
        self.assertEqual(result["value"], 2)
        self.assertEqual(result["nasa_last_seen"], "now")
        entity.update_value.assert_called_once()

    def test_user_disabled_entity_is_not_enabled_by_first_value(self):
        self.registry.async_get.return_value = SimpleNamespace(disabled_by="user")

        self.coordinator._enable_entity_after_first_value("sensor", "test", {}, Mock(entity_id="sensor.test"))

        self.registry.async_update_entity.assert_not_called()

    def test_integration_disabled_entity_is_enabled_without_duplicate_pending_add(self):
        self.registry.async_get.return_value = SimpleNamespace(disabled_by="integration")
        adder = Mock()
        factory = Mock()
        self.coordinator.register_entity_adder("sensor", adder, factory)
        data = {"nasa_name": "NASA_TEST", "_entity_add_pending": True}

        self.coordinator._enable_entity_after_first_value("sensor", "test", data, Mock(entity_id="sensor.test"))

        self.registry.async_update_entity.assert_called_once_with("sensor.test", disabled_by=None)
        adder.assert_not_called()
        factory.assert_not_called()

    async def test_concurrent_statistics_increments_are_not_lost(self):
        await asyncio.gather(*(self.coordinator._inc_stat("packets_requested") for _ in range(100)))

        self.assertEqual(self.coordinator.stats["packets_requested"], 100)

    async def test_stop_closes_transports_and_cancels_all_owned_tasks(self):
        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(6)]
        self.coordinator._tcp_read_task, self.coordinator._tcp_write_task, self.coordinator._tcp_task = tasks[:3]
        self.coordinator._tcp_polling_tasks = {"fast": tasks[3]}
        self.coordinator._diagnostic_task = tasks[4]
        self.coordinator._packet_workers = [tasks[5]]
        writer = Mock(wait_closed=AsyncMock())
        reader = SimpleNamespace(_transport=Mock())
        self.coordinator._tcp_writer = writer
        self.coordinator._tcp_reader = reader

        await self.coordinator.stop()

        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()
        reader._transport.abort.assert_called_once()
        self.assertTrue(all(task.cancelled() for task in tasks))
        self.assertFalse(self.coordinator.running)
        self.assertFalse(self.coordinator._packet_workers)
        self.assertFalse(self.coordinator._tcp_polling_tasks)
        self.assertIsNone(self.coordinator.processor)
        self.assertIsNone(self.coordinator.producer)

    async def test_connection_failure_retries_and_cancellation_exits(self):
        self.coordinator.ip, self.coordinator.port = "127.0.0.1", 8899
        with patch("asyncio.open_connection", AsyncMock(side_effect=[ConnectionRefusedError(), asyncio.CancelledError()])) as connect:
            with patch("asyncio.sleep", AsyncMock()) as delay:
                await self.coordinator._tcp_loop()

        self.assertEqual(connect.await_count, 2)
        delay.assert_awaited_once_with(5)
        self.assertIsNone(self.coordinator._tcp_writer)

    async def test_reconnect_discards_previous_cop_inputs_before_accepting_new_packets(self):
        processor_path = COORDINATOR_PATH.with_name("message_processor.py")
        tree = ast.parse(processor_path.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, (ast.Assign, ast.ClassDef))]
        namespace = {**self.namespace, "timedelta": timedelta}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(processor_path), "exec"), namespace)
        processor = namespace["MessageProcessor"](self.coordinator.hass, self.coordinator)
        processor._get_platform = Mock(return_value="sensor")
        self.coordinator.processor = processor
        self.coordinator.only_known_entities = False
        self.coordinator.force_refresh = False
        self.coordinator.ip, self.coordinator.port = "127.0.0.1", 8899
        cop_key = processor._normalize_name("NASA_EHSSENTINEL_COP")
        for name in ("NASA_EHSSENTINEL_COP", "NASA_OUTDOOR_OPERATION_STATUS",
                     "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", "LVAR_IN_GENERATED_POWER_LAST_MINUTE",
                     "NASA_EHSSENTINEL_HEAT_OUTPUT"):
            self.coordinator.data["sensor"][processor._normalize_name(name)] = {"value": None, "seen_once": False}
        await processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL")
        await processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2)
        await processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5)
        self.assertEqual(self.coordinator.data["sensor"][cop_key]["value"], 4)

        first_reader, second_reader = object(), object()
        first_writer = Mock(wait_closed=AsyncMock(), get_extra_info=Mock(return_value=None))
        second_writer = Mock(wait_closed=AsyncMock(), get_extra_info=Mock(return_value=None))
        cop_after_first_new_input = []

        async def read_connection(reader):
            if reader is first_reader:
                raise ConnectionResetError("connection lost during unobserved status changes")
            await processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.4)
            cop_after_first_new_input.append(self.coordinator.data["sensor"][cop_key]["value"])
            await processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_SAFETY")
            await processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.4)
            await processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 1.2)
            cop_after_first_new_input.append(self.coordinator.data["sensor"][cop_key]["value"])
            self.coordinator.running = False

        original_sleep = asyncio.sleep

        async def yield_to_connection_tasks(delay):
            await original_sleep(0)

        self.coordinator._tcp_read = read_connection
        self.coordinator._tcp_write = AsyncMock()
        with patch("asyncio.open_connection", AsyncMock(side_effect=[
            (first_reader, first_writer), (second_reader, second_writer),
        ])) as connect:
            with patch("asyncio.sleep", yield_to_connection_tasks):
                await asyncio.wait_for(self.coordinator._tcp_loop(), timeout=2)

        self.assertEqual(connect.await_count, 2)
        first_writer.close.assert_called_once()
        second_writer.close.assert_called_once()
        self.assertEqual(cop_after_first_new_input, [None, 3])
        self.assertIsNone(self.coordinator.data["sensor"][cop_key]["value"])

    async def test_repeated_tcp_write_setup_does_not_duplicate_running_poller(self):
        self.coordinator.indoor_address = {"address": 1}
        self.coordinator.outdoor_address = {"address": 2}
        self.coordinator.writemode = False
        self.coordinator.polling = True
        self.coordinator.polling_yaml = {"fetch_interval": [{"name": "fast", "enable": True}]}
        existing = asyncio.create_task(asyncio.Event().wait())
        self.coordinator._tcp_polling_tasks = {"fast": existing}
        try:
            with patch("asyncio.sleep", AsyncMock()):
                await self.coordinator._tcp_write()
                await self.coordinator._tcp_write()
            self.assertEqual(self.coordinator._tcp_polling_tasks, {"fast": existing})
        finally:
            existing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await existing


if __name__ == "__main__":
    unittest.main()