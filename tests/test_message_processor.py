"""Derived sensor tests runnable without importing Home Assistant."""

import ast
from datetime import datetime, timedelta
import logging
from pathlib import Path
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


PROCESSOR_PATH = Path(__file__).resolve().parents[1] / "custom_components/ehs_sentinel/message_processor.py"


class DerivedSensorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(PROCESSOR_PATH.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, (ast.Assign, ast.ClassDef))]
        namespace = {
            "logging": logging, "datetime": datetime, "timedelta": timedelta,
            "PLATFORM_SENSOR": "sensor", "traceback": traceback,
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PROCESSOR_PATH), "exec"), namespace)
        coordinator = SimpleNamespace(_logger=Mock(), data={"sensor": {}}, extended_logging=False)
        self.processor = namespace["MessageProcessor"](None, coordinator)
        self.protocol_message = self.processor.protocol_message
        self.processor.protocol_message = AsyncMock()
        self.dt = "2026-11-07T13:10:19"
        self.delta_sources = namespace["DELTA_SOURCES"]

    async def record_message(self, msgname, msgvalue):
        self.processor.value_store[msgname] = {"val": msgvalue, "dt": self.dt}
        self.processor.coordinator.data["sensor"][self.processor._normalize_name(msgname)] = {
            "value": msgvalue, "nasa_last_seen": self.dt,
        }

    def restore_mode_totals(self, missing_key=None):
        for source, value in (("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM", 100),
                              ("LVAR_IN_TOTAL_GENERATED_POWER", 300)):
            for key in self.delta_sources[source][:2]:
                if key != missing_key:
                    self.processor.coordinator.data["sensor"][self.processor._normalize_name(key)] = {
                        "value": value, "nasa_last_seen": "2026-11-07T13:09:19",
                    }
        self.processor.value_store["ENUM_IN_FSV_3011"] = {"val": "No"}
        self.processor.dhw_power_store = {"val": "OFF", "dt": "2026-11-07T13:00:00"}
        self.processor.protocol_message.side_effect = self.record_message

    async def send_delta(self, source, value):
        await self.processor._handle_mode_delta(source, value, self.dt)
        await self.record_message(source, value)

    async def update_data(self, parsed):
        for platform, values in parsed.items():
            for key, payload in values.items():
                self.processor.coordinator.data.setdefault(platform, {}).setdefault(key, {}).update(payload)

    def enable_protocol_processing(self):
        coordinator = self.processor.coordinator
        coordinator.should_process_nasa_name = Mock(return_value=True)
        coordinator.force_refresh = False
        coordinator.confirm_read = Mock()
        coordinator.confirm_write = Mock()
        coordinator.update_data_safe = AsyncMock(side_effect=self.update_data)
        self.processor._get_platform = Mock(return_value="sensor")
        self.processor.protocol_message = self.protocol_message

    async def test_instantaneous_cop_ignores_values_cached_before_start(self):
        self.processor.value_store = {
            "LVAR_IN_GENERATED_POWER_LAST_MINUTE": {"val": 0, "dt": self.dt},
            "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT": {"val": 0.013, "dt": self.dt},
        }
        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)
        self.processor.protocol_message.reset_mock()

        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2.842, self.dt)

        self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", None)

    async def test_incomplete_heat_pair_is_reset_together(self):
        consumed = "NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"
        generated = "NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE"
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.restore_mode_totals(missing_key=consumed)
        self.processor.coordinator.data["sensor"][self.processor._normalize_name(source)] = {
            "value": 10583, "nasa_last_seen": "2026-11-07T13:09:19",
        }

        await self.processor._handle_mode_delta(source, 10584, self.dt)

        self.assertEqual(self.processor.value_store[consumed]["val"], 0)
        self.assertEqual(self.processor.value_store[generated]["val"], 0)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE"]["val"], 100)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE"]["val"], 300)

    async def test_fresh_inputs_allow_cop_below_previous_power_threshold(self):
        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)
        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 0.2, self.dt)
        for power in (None, -0.1, 0, 0.1, 0.3, 0.301, 0.51):
            with self.subTest(power=power):
                self.processor.protocol_message.reset_mock()

                await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", power, self.dt)

                expected = None if power is None or power <= 0 else round(0.2 / power, 3)
                self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", expected)

    async def test_missing_cop_inputs_clear_previous_ratio(self):
        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)
        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2.842, self.dt)
        await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.51, self.dt)
        self.processor.protocol_message.reset_mock()

        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", None, self.dt)

        self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", None)

    async def test_lifetime_cop_has_no_instantaneous_power_threshold(self):
        self.processor.value_store["LVAR_IN_TOTAL_GENERATED_POWER"] = {"val": 0.31}

        await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM", 0.1, self.dt)

        self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_TOTAL_COP", 3.1)

    async def test_complete_mode_pairs_are_preserved(self):
        self.restore_mode_totals()
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.processor.coordinator.data["sensor"][self.processor._normalize_name(source)] = {
            "value": 10583, "nasa_last_seen": "2026-11-07T13:09:19",
        }

        await self.send_delta(source, 10584)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"]["val"], 101)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE"]["val"], 300)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE"]["val"], 100)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_DHW_MODE"]["val"], 300)

    async def test_missing_generated_total_resets_pair_and_both_meter_baselines(self):
        generated = "NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE"
        consumed = "NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"
        gen_source = "LVAR_IN_TOTAL_GENERATED_POWER"
        cons_source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.restore_mode_totals(missing_key=generated)
        for source, value in ((gen_source, 3501), (cons_source, 10583)):
            self.processor.coordinator.data["sensor"][self.processor._normalize_name(source)] = {
                "value": value, "nasa_last_seen": "2026-11-07T13:09:19",
            }

        await self.send_delta(gen_source, 3502)
        await self.send_delta(cons_source, 10584)

        self.assertEqual(self.processor.value_store[generated]["val"], 0)
        self.assertEqual(self.processor.value_store[consumed]["val"], 0)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DAILY_GENERATED_POWER"]["val"], 1)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DAILY_CONSUMED_POWER"]["val"], 1)

        self.dt = "2026-11-07T13:11:19"
        await self.send_delta(gen_source, 3505)
        await self.send_delta(cons_source, 10585)

        self.assertEqual(self.processor.value_store[generated]["val"], 3)
        self.assertEqual(self.processor.value_store[consumed]["val"], 1)

    async def test_fresh_totals_do_not_inherit_absolute_lifetime_meters(self):
        self.restore_mode_totals()
        self.processor.coordinator.data["sensor"].clear()
        cons_source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        gen_source = "LVAR_IN_TOTAL_GENERATED_POWER"

        await self.send_delta(cons_source, 10583)
        await self.send_delta(gen_source, 32865)

        for source in (cons_source, gen_source):
            for key in self.delta_sources[source][:2]:
                self.assertEqual(self.processor.value_store[key]["val"], 0)

        self.dt = "2026-11-07T13:11:19"
        await self.send_delta(cons_source, 10584)
        await self.send_delta(gen_source, 32868)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"]["val"], 1)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE"]["val"], 3)

    async def test_explicit_reset_restarts_both_meter_baselines(self):
        self.restore_mode_totals()
        await self.processor._initialize_mode_energy_totals(self.dt)
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.processor.value_store[source] = {"val": 10583, "dt": "2026-11-07T13:09:19"}

        await self.processor.development_tool("reset_all_counters")
        await self.send_delta(source, 10584)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"]["val"], 0)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE"]["val"], 0)
        self.assertEqual(self.processor._mode_energy_baselines_pending, {"LVAR_IN_TOTAL_GENERATED_POWER"})

    async def test_real_protocol_updates_cop_after_both_startup_inputs(self):
        self.enable_protocol_processing()

        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_STOP")
        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.51)
        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL")
        await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2.842)
        self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"])

        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.51)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], 5.573)
        self.processor._logger.error.assert_not_called()

    async def test_stop_clears_inputs_and_restart_waits_for_both_new_values(self):
        self.enable_protocol_processing()
        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL")
        await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2.842)
        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.51)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], 5.573)

        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_STOP")

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], 0)
        self.assertIsNone(self.processor._cop_generated_power)
        self.assertIsNone(self.processor._cop_consumed_power)

        await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 8)
        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 2)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], 0)
        self.assertIsNone(self.processor._cop_generated_power)
        self.assertIsNone(self.processor._cop_consumed_power)

        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL")
        self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"])
        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.2)
        self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"])
        await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 0.6)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], 3)
        self.processor._logger.error.assert_not_called()

    async def test_repeated_operation_status_preserves_fresh_inputs(self):
        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)
        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2, self.dt)
        await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5, self.dt)
        self.processor.protocol_message.reset_mock()

        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)

        self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", 4)
        self.assertEqual(self.processor._cop_generated_power, 2)
        self.assertEqual(self.processor._cop_consumed_power, 0.5)

    async def test_startup_waits_for_operation_status_and_then_fresh_inputs(self):
        self.enable_protocol_processing()
        await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2)
        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5)
        self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"])

        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL")
        await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2)
        self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"])
        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], 4)
        self.processor._logger.error.assert_not_called()

    async def test_zero_heat_is_a_valid_fresh_input(self):
        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)
        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 0, self.dt)
        self.processor.protocol_message.reset_mock()

        await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5, self.dt)

        self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", 0)

    async def test_cop_inputs_are_not_shared_between_processor_instances(self):
        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)
        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2, self.dt)
        await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5, self.dt)
        other = type(self.processor)(None, self.processor.coordinator)
        other.protocol_message = AsyncMock()

        await other._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_STOP", self.dt)

        self.assertEqual(self.processor._cop_operation_status, "OP_NORMAL")
        self.assertEqual(self.processor._cop_generated_power, 2)
        self.assertEqual(self.processor._cop_consumed_power, 0.5)
        self.assertIsNone(other._cop_generated_power)
        self.assertIsNone(other._cop_consumed_power)
        other.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", 0)

    async def test_each_active_status_transition_requires_both_fresh_inputs(self):
        statuses = ("OP_NORMAL", "OP_SAFETY", "OP_DEICE")
        for previous in statuses:
            for current in statuses:
                if previous == current:
                    continue
                with self.subTest(previous=previous, current=current):
                    await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_STOP", self.dt)
                    await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", previous, self.dt)
                    await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2, self.dt)
                    await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5, self.dt)
                    self.processor.protocol_message.reset_mock()

                    await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", current, self.dt)

                    self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", None)
                    self.assertIsNone(self.processor._cop_generated_power)
                    self.assertIsNone(self.processor._cop_consumed_power)
                    self.processor.protocol_message.reset_mock()

                    await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.2, self.dt)

                    self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", None)
                    self.processor.protocol_message.reset_mock()

                    await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 0.6, self.dt)

                    self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", 3)
                    self.processor.protocol_message.reset_mock()

                    await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", current, self.dt)

                    self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", 3)

    async def test_real_protocol_calculates_safety_and_deice_cop_and_keeps_counters(self):
        self.enable_protocol_processing()
        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_STOP")
        for status, heat in (("OP_SAFETY", 0.6), ("OP_NORMAL", 0.8),
                             ("OP_DEICE", -0.2), ("OP_NORMAL", 1)):
            with self.subTest(status=status):
                await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", status)
                self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"])
                await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", heat)
                self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"])
                await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.2)
                self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], round(heat / 0.2, 3))

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_START_COUNTER"]["val"], 1)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DEFROST_COUNTER"]["val"], 1)
        self.processor._logger.error.assert_not_called()

    async def test_frequency_packets_do_not_control_cop(self):
        self.enable_protocol_processing()
        await self.processor.protocol_message("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL")
        await self.processor.protocol_message("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2)
        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5)

        for frequency in (0, 8, 40):
            with self.subTest(frequency=frequency):
                await self.processor.protocol_message("NASA_OUTDOOR_COMP1_RUN_HZ", frequency)

                self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_COP"]["val"], 4)
                self.assertEqual(self.processor._cop_operation_status, "OP_NORMAL")
                self.assertEqual(self.processor._cop_generated_power, 2)
                self.assertEqual(self.processor._cop_consumed_power, 0.5)

        self.processor._logger.error.assert_not_called()

    async def test_unknown_operation_status_clears_inputs_and_withholds_cop(self):
        await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", "OP_NORMAL", self.dt)
        await self.processor._handle_cop("LVAR_IN_GENERATED_POWER_LAST_MINUTE", 2, self.dt)
        await self.processor._handle_cop("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT", 0.5, self.dt)

        for status in (None, "OP_UNKNOWN"):
            with self.subTest(status=status):
                self.processor.protocol_message.reset_mock()
                await self.processor._handle_cop("NASA_OUTDOOR_OPERATION_STATUS", status, self.dt)
                self.processor.protocol_message.assert_awaited_once_with("NASA_EHSSENTINEL_COP", None)
                self.assertIsNone(self.processor._cop_generated_power)
                self.assertIsNone(self.processor._cop_consumed_power)

    async def test_real_protocol_resets_orphaned_totals_before_computing_mode_cop(self):
        self.restore_mode_totals(missing_key="NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE")
        self.enable_protocol_processing()

        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM", 10583)
        await self.processor.protocol_message("LVAR_IN_TOTAL_GENERATED_POWER", 32865)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE"]["val"], 0)
        self.processor._logger.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()