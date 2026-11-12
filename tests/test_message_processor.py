"""Derived sensor tests runnable without importing Home Assistant."""

import ast
import asyncio
from datetime import datetime, timedelta
import logging
from pathlib import Path
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


PROCESSOR_PATH = Path(__file__).resolve().parents[1] / "custom_components/ehs_sentinel/message_processor.py"


class DerivedSensorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(PROCESSOR_PATH.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, (ast.Assign, ast.ClassDef))]
        namespace = {
            "logging": logging, "datetime": datetime, "timedelta": timedelta, "asyncio": asyncio,
            "PLATFORM_SENSOR": "sensor", "traceback": traceback,
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PROCESSOR_PATH), "exec"), namespace)
        coordinator = SimpleNamespace(_logger=Mock(), data={"sensor": {}}, extended_logging=False)
        self.processor = namespace["MessageProcessor"](None, coordinator)
        self.protocol_message = self.processor.protocol_message
        self.processor.protocol_message = AsyncMock()
        self.dt = "2026-11-07T13:10:19"
        self.delta_sources = namespace["DELTA_SOURCES"]
        self.namespace = namespace

    async def test_mode_switch_assigns_elapsed_delta_to_previous_mode(self):
        for source, targets in self.delta_sources.items():
            for current_mode in ("ON", "OFF"):
                with self.subTest(source=source, current_mode=current_mode):
                    self.processor.value_store = {
                        source: {"val": 100, "dt": "2026-11-07T13:10:00"},
                        "ENUM_IN_FSV_3011": {"val": "Yes"},
                        **{key: {"val": 0, "dt": "2026-11-07T13:10:00"} for key in targets},
                    }
                    self.processor._mode_energy_totals_initialized = True
                    self.processor.dhw_power_store = {"val": current_mode, "dt": "2026-11-07T13:10:15"}
                    self.processor.protocol_message.side_effect = self.record_message
                    self.dt = "2026-11-07T13:11:00"

                    await self.send_delta(source, 112)

                    dhw, heat, daily_dhw, daily_heat, daily = targets
                    expected_dhw, expected_heat = (9, 3) if current_mode == "ON" else (3, 9)
                    self.assertEqual(self.processor.value_store[dhw]["val"], expected_dhw)
                    self.assertEqual(self.processor.value_store[heat]["val"], expected_heat)
                    self.assertEqual(self.processor.value_store[daily_dhw]["val"], expected_dhw)
                    self.assertEqual(self.processor.value_store[daily_heat]["val"], expected_heat)
                    self.assertEqual(self.processor.value_store[daily]["val"], 12)

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

    async def test_incomplete_heat_pair_preserves_existing_total(self):
        consumed = "NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"
        generated = "NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE"
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.restore_mode_totals(missing_key=consumed)
        self.processor.coordinator.data["sensor"][self.processor._normalize_name(source)] = {
            "value": 10583, "nasa_last_seen": "2026-11-07T13:09:19",
        }

        await self.processor._handle_mode_delta(source, 10584, self.dt)

        self.assertIsNone(self.processor.value_store.get(consumed, {}).get("val"))
        self.assertEqual(self.processor.value_store[generated]["val"], 300)
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

    async def test_missing_generated_total_does_not_reset_consumed_total(self):
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

        self.assertIsNone(self.processor.value_store.get(generated, {}).get("val"))
        self.assertEqual(self.processor.value_store[consumed]["val"], 101)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DAILY_GENERATED_POWER"]["val"], 1)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DAILY_CONSUMED_POWER"]["val"], 1)

        self.dt = "2026-11-07T13:11:19"
        await self.send_delta(gen_source, 3505)
        await self.send_delta(cons_source, 10585)

        self.assertIsNone(self.processor.value_store.get(generated, {}).get("val"))
        self.assertEqual(self.processor.value_store[consumed]["val"], 102)

    async def test_late_total_restore_is_loaded_with_source_already_initialized(self):
        for source, targets in self.delta_sources.items():
            with self.subTest(source=source):
                self.setUp()
                self.restore_mode_totals(missing_key=targets[1])
                self.processor.value_store[source] = {"val": 100, "dt": "2026-11-07T13:09:19"}

                await self.send_delta(source, 101)

                self.assertIsNone(self.processor.value_store.get(targets[1], {}).get("val"))
                self.processor.coordinator.data["sensor"][self.processor._normalize_name(targets[1])] = {
                    "value": 1800, "nasa_last_seen": "2026-11-07T13:09:19",
                }
                self.dt = "2026-11-07T13:11:19"
                await self.send_delta(source, 102)

                self.assertEqual(self.processor.value_store[targets[1]]["val"], 1800)
                self.dt = "2026-11-07T13:12:19"
                await self.send_delta(source, 103)
                self.assertEqual(self.processor.value_store[targets[1]]["val"], 1801)

    async def test_fresh_totals_do_not_inherit_absolute_lifetime_meters(self):
        self.restore_mode_totals()
        self.processor.coordinator.data["sensor"].clear()
        for targets in self.delta_sources.values():
            for key in targets[:2]:
                self.processor.coordinator.data["sensor"][self.processor._normalize_name(key)] = {
                    "_total_is_new": True,
                }
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
        self.assertEqual(self.processor._mode_energy_baselines_pending, {
            "LVAR_IN_TOTAL_GENERATED_POWER", "LVAR_IN_MINUTES_ACTIVE",
        })

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

    async def test_midnight_resets_daily_counters_once_and_preserves_totals(self):
        self.restore_mode_totals()
        self.enable_protocol_processing()
        self.processor.coordinator.extended_logging = True
        daily_keys = self.namespace["DAILY_MESSAGES"]
        for key in daily_keys:
            self.processor.value_store[key] = {"val": 5, "dt": "2026-11-07T23:59:59"}
        self.processor.last_dt = "2026-11-07T23:59:59"
        snapshot = dict(self.processor.coordinator.data["sensor"])
        clock = Mock(wraps=datetime)
        clock.now.return_value = datetime(2026, 11, 8, 0, 0, 1)

        with patch.dict(self.namespace, {"datetime": clock}):
            await self.processor.protocol_message("NASA_TEST", 1)
            await self.processor.protocol_message("NASA_TEST", 2)

        for key in daily_keys:
            self.assertEqual(self.processor.value_store[key]["val"], 0)
        for key, data in snapshot.items():
            self.assertEqual(self.processor.coordinator.data["sensor"][key], data)
        resets = [call for call in self.processor._logger.info.call_args_list
                  if "New day detected" in str(call)]
        self.assertEqual(len(resets), 1)
        self.processor._logger.error.assert_not_called()

    async def test_restart_discards_yesterdays_daily_values_but_restores_totals(self):
        self.restore_mode_totals()
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        for key in (source, *self.delta_sources[source][2:]):
            self.processor.coordinator.data["sensor"][self.processor._normalize_name(key)] = {
                "value": 100 if key == source else 7,
                "nasa_last_seen": "2026-11-06T23:59:00",
            }

        await self.send_delta(source, 101)

        dhw, heat, daily_dhw, daily_heat, daily = self.delta_sources[source]
        self.assertEqual(self.processor.value_store[dhw]["val"], 100)
        self.assertEqual(self.processor.value_store[heat]["val"], 101)
        self.assertEqual(self.processor.value_store[daily_dhw]["val"], 0)
        self.assertEqual(self.processor.value_store[daily_heat]["val"], 1)
        self.assertEqual(self.processor.value_store[daily]["val"], 1)

    async def test_meter_rollback_is_ignored_then_positive_deltas_resume(self):
        self.restore_mode_totals()
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.processor.value_store[source] = {"val": 100, "dt": "2026-11-07T13:09:19"}
        await self.processor._initialize_mode_energy_totals(self.dt)
        self.processor.protocol_message.reset_mock()

        await self.send_delta(source, 0)

        self.processor.protocol_message.assert_not_awaited()
        self.dt = "2026-11-07T13:11:19"
        await self.send_delta(source, 1)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"]["val"], 101)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DAILY_CONSUMED_POWER"]["val"], 1)

    async def test_daily_reset_leaves_all_total_and_live_values_unchanged(self):
        self.restore_mode_totals()
        await self.processor._initialize_mode_energy_totals(self.dt)
        protected = {
            "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM": 10611,
            "LVAR_IN_TOTAL_GENERATED_POWER": 32865,
            "NASA_EHSSENTINEL_TOTAL_COP": 3.1,
            "NASA_EHSSENTINEL_COP": 4,
            "NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE": 3,
            "NASA_EHSSENTINEL_TOTAL_COP_DHW_MODE": 3,
            "NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_HEAT_MODE": 120,
            "NASA_EHSSENTINEL_TOTAL_MINUTES_ACTIVE_DHW_MODE": 60,
        }
        for key, value in protected.items():
            await self.record_message(key, value)
        for key in self.namespace["DAILY_MESSAGES"]:
            await self.record_message(key, 5)
        totals = {key: dict(value) for key, value in self.processor.value_store.items()
                  if key not in self.namespace["DAILY_MESSAGES"]}

        await self.processor.development_tool("reset_daily_counters")

        for key in self.namespace["DAILY_MESSAGES"]:
            self.assertEqual(self.processor.value_store[key]["val"], 0)
        for key, value in totals.items():
            self.assertEqual(self.processor.value_store[key], value)
        self.assertFalse(self.processor._mode_energy_baselines_pending)

    async def test_all_reset_clears_mode_pairs_but_not_hardware_lifetime_or_live_cop(self):
        self.restore_mode_totals()
        await self.processor._initialize_mode_energy_totals(self.dt)
        protected = {
            "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM": 10611,
            "LVAR_IN_TOTAL_GENERATED_POWER": 32865,
            "NASA_EHSSENTINEL_TOTAL_COP": 3.1,
            "NASA_EHSSENTINEL_COP": 4,
        }
        for key, value in protected.items():
            await self.record_message(key, value)

        await self.processor.development_tool("reset_all_counters")

        for source, targets in self.delta_sources.items():
            for key in targets:
                self.assertEqual(self.processor.value_store[key]["val"], 0)
        for key in ("NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE", "NASA_EHSSENTINEL_TOTAL_COP_DHW_MODE"):
            self.assertEqual(self.processor.value_store[key]["val"], 0)
        for key, value in protected.items():
            self.assertEqual(self.processor.value_store[key]["val"], value)

    async def test_invalid_raw_packet_value_is_ignored_and_next_message_is_processed(self):
        self.processor.coordinator.nasa_repo = {
            "NASA_TEST": {"address": "0x8000", "invalid_raw_values": ["ff"]},
        }
        self.processor.coordinator.determine_value = AsyncMock(return_value=1)
        packet = SimpleNamespace(
            packet_source_address_class=SimpleNamespace(name="Outdoor"),
            packet_messages=[SimpleNamespace(packet_message=0x8000, packet_message_type=0, packet_payload=payload)
                             for payload in (b"\xff", b"\x01")],
        )

        await self.processor.process_message(packet)

        self.processor.coordinator.determine_value.assert_awaited_once_with(b"\x01", "NASA_TEST", 0)
        self.processor.protocol_message.assert_awaited_once_with("NASA_TEST", 1)

    async def test_decode_failure_does_not_block_following_message(self):
        self.processor.coordinator.nasa_repo = {"NASA_TEST": {"address": "0x8000"}}
        self.processor.coordinator.determine_value = AsyncMock(side_effect=[ValueError("invalid"), 2])
        packet = SimpleNamespace(
            packet_source_address_class=SimpleNamespace(name="Outdoor"),
            packet_messages=[SimpleNamespace(packet_message=0x8000, packet_message_type=0, packet_payload=payload)
                             for payload in (b"\x01", b"\x02")],
        )

        await self.processor.process_message(packet)

        self.assertEqual(self.processor.determine_value_store["NASA_TEST"], 1)
        self.processor.protocol_message.assert_awaited_once_with("NASA_TEST", 2)

    async def test_manual_mode_overrides_valve_and_keeps_delta_sum(self):
        self.restore_mode_totals()
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.processor.value_store[source] = {"val": 100, "dt": "2026-11-07T13:09:19"}
        self.processor.set_mode = "DHW"

        await self.send_delta(source, 104)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_DHW_MODE"]["val"], 104)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"]["val"], 100)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DAILY_CONSUMED_POWER"]["val"], 4)

    async def test_parallel_meter_updates_do_not_lose_energy_or_rewind_source(self):
        self.restore_mode_totals()
        self.enable_protocol_processing()
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        self.processor.value_store[source] = {"val": 100, "dt": "2026-11-07T13:09:19"}
        entered = asyncio.Event()
        release = asyncio.Event()
        daily_key = self.processor._normalize_name("NASA_EHSSENTINEL_DAILY_CONSUMED_POWER")

        async def blocked_update(parsed):
            if daily_key in parsed.get("sensor", {}) and not entered.is_set():
                entered.set()
                await release.wait()
            await self.update_data(parsed)

        self.processor.coordinator.update_data_safe.side_effect = blocked_update
        clock = Mock(wraps=datetime)
        clock.now.side_effect = (datetime(2026, 11, 7, 13, 10, 19) + timedelta(seconds=offset)
                                 for offset in range(100))
        second_started = asyncio.Event()

        async def process_second():
            second_started.set()
            await self.processor.protocol_message(source, 102)

        with patch.dict(self.namespace, {"datetime": clock}):
            first = asyncio.create_task(self.processor.protocol_message(source, 101))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                second = asyncio.create_task(process_second())
                await asyncio.wait_for(second_started.wait(), timeout=1)
            finally:
                release.set()
                await asyncio.gather(first, *([second] if second is not None else []))

        self.assertEqual(self.processor.value_store[source]["val"], 102)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE"]["val"], 102)
        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_DAILY_CONSUMED_POWER"]["val"], 2)
        self.processor._logger.error.assert_not_called()

    async def test_cancelled_message_releases_protocol_lock_for_next_message(self):
        self.enable_protocol_processing()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_update(parsed):
            if not entered.is_set():
                entered.set()
                await release.wait()
            await self.update_data(parsed)

        self.processor.coordinator.update_data_safe.side_effect = blocked_update
        first = asyncio.create_task(self.processor.protocol_message("NASA_TEST", 1))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
        finally:
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first

        await asyncio.wait_for(self.processor.protocol_message("NASA_TEST", 2), timeout=1)

        self.assertEqual(self.processor.value_store["NASA_TEST"]["val"], 2)
        self.assertIsNone(self.processor._protocol_owner)
        self.assertFalse(self.processor._protocol_lock.locked())

    async def test_interval_crossing_midnight_only_counts_new_day_share(self):
        self.enable_protocol_processing()
        clock = Mock(wraps=datetime)
        clock.now.return_value = datetime(2026, 11, 8, 0, 1)
        for source, targets in self.delta_sources.items():
            with self.subTest(source=source):
                old_dt = "2026-11-07T23:59:00"
                self.processor.value_store = {
                    source: {"val": 100, "dt": old_dt},
                    "ENUM_IN_FSV_3011": {"val": "No"},
                    **{key: {"val": 100 if key in targets[:2] else 10, "dt": old_dt}
                       for key in targets},
                }
                self.processor.coordinator.data["sensor"].clear()
                self.processor._mode_energy_totals_initialized = True
                self.processor.dhw_power_store = {"val": "OFF", "dt": "2026-11-07T23:00:00"}
                self.processor.last_dt = old_dt

                with patch.dict(self.namespace, {"datetime": clock}):
                    await self.processor.protocol_message(source, 112)

                dhw, heat, daily_dhw, daily_heat, daily = targets
                self.assertEqual(self.processor.value_store[heat]["val"], 112)
                self.assertEqual(self.processor.value_store[dhw]["val"], 100)
                self.assertEqual(self.processor.value_store[daily]["val"], 6)
                self.assertEqual(self.processor.value_store[daily_heat]["val"], 6)
                self.assertEqual(self.processor.value_store[daily_dhw]["val"], 0)
                self.processor._logger.error.assert_not_called()

    async def test_reset_in_progress_does_not_mix_old_meter_deltas_with_reset_counters(self):
        self.enable_protocol_processing()
        source = "LVAR_IN_MINUTES_ACTIVE"
        targets = self.delta_sources[source]
        old_dt = "2026-11-07T13:00:00"
        self.processor.value_store = {
            source: {"val": 100, "dt": old_dt},
            "ENUM_IN_FSV_3011": {"val": "No"},
            **{key: {"val": 10, "dt": old_dt} for key in targets},
        }
        self.processor.dhw_power_store = {"val": "ON", "dt": old_dt}
        entered = asyncio.Event()
        release = asyncio.Event()
        packet_started = asyncio.Event()
        first_daily_key = self.processor._normalize_name(targets[2])

        async def blocked_reset_update(parsed):
            if first_daily_key in parsed.get("sensor", {}) and not entered.is_set():
                entered.set()
                await release.wait()
            await self.update_data(parsed)

        async def process_packet():
            packet_started.set()
            await self.processor.protocol_message(source, 112)

        self.processor.coordinator.update_data_safe.side_effect = blocked_reset_update
        clock = Mock(wraps=datetime)
        clock.now.side_effect = (datetime(2026, 11, 7, 13, 1) + timedelta(seconds=offset)
                                 for offset in range(100))
        with patch.dict(self.namespace, {"datetime": clock}):
            reset = asyncio.create_task(self.processor.development_tool("reset_all_counters"))
            packet = None
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                packet = asyncio.create_task(process_packet())
                await asyncio.wait_for(packet_started.wait(), timeout=1)
            finally:
                release.set()
                await asyncio.gather(reset, *([packet] if packet is not None else []))

        for key in targets:
            self.assertEqual(self.processor.value_store[key]["val"], 0, key)
        self.processor._logger.error.assert_not_called()

    async def test_two_valve_switches_between_meter_packets_preserve_each_mode_share(self):
        self.enable_protocol_processing()
        for source, targets in self.delta_sources.items():
            with self.subTest(source=source):
                old_dt = "2026-11-07T13:00:00"
                self.processor.coordinator.data["sensor"].clear()
                self.processor.value_store = {
                    source: {"val": 100, "dt": old_dt},
                    "NASA_DHW_VALVE": {"val": "ROOM", "dt": old_dt},
                    "ENUM_IN_FSV_3011": {"val": "Yes"},
                    **{key: {"val": 0, "dt": old_dt} for key in targets},
                }
                self.processor._mode_energy_totals_initialized = True
                self.processor._mode_history = []
                self.processor.dhw_power_store = {"val": "OFF", "dt": "2026-11-07T12:59:00"}
                self.processor.last_dt = old_dt
                clock = Mock(wraps=datetime)
                clock.now.side_effect = iter([
                    datetime(2026, 11, 7, 13, 0, 20),
                    datetime(2026, 11, 7, 13, 0, 40),
                    *[datetime(2026, 11, 7, 13, 1)] * 30,
                ])

                with patch.dict(self.namespace, {"datetime": clock}):
                    await self.processor.protocol_message("NASA_DHW_VALVE", "TANK")
                    await self.processor.protocol_message("NASA_DHW_VALVE", "ROOM")
                    await self.processor.protocol_message(source, 160)

                dhw, heat, daily_dhw, daily_heat, daily = targets
                self.assertEqual(self.processor.value_store[dhw]["val"], 20)
                self.assertEqual(self.processor.value_store[heat]["val"], 40)
                self.assertEqual(self.processor.value_store[daily_dhw]["val"], 20)
                self.assertEqual(self.processor.value_store[daily_heat]["val"], 40)
                self.assertEqual(self.processor.value_store[daily]["val"], 60)
                self.processor._logger.error.assert_not_called()

    async def test_midnight_valve_switch_splits_today_and_total_intervals_separately(self):
        self.enable_protocol_processing()
        source = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM"
        targets = self.delta_sources[source]
        old_dt = "2026-11-07T23:59:00"
        self.processor.value_store = {
            source: {"val": 100, "dt": old_dt},
            "NASA_DHW_VALVE": {"val": "ROOM", "dt": old_dt},
            "ENUM_IN_FSV_3011": {"val": "Yes"},
            **{key: {"val": 0, "dt": old_dt} for key in targets},
        }
        self.processor._mode_energy_totals_initialized = True
        self.processor.dhw_power_store = {"val": "OFF", "dt": "2026-11-07T23:00:00"}
        self.processor.last_dt = old_dt
        clock = Mock(wraps=datetime)
        clock.now.return_value = datetime(2026, 11, 8, 0, 0, 30)

        with patch.dict(self.namespace, {"datetime": clock}):
            await self.processor.protocol_message("NASA_DHW_VALVE", "TANK")
            clock.now.return_value = datetime(2026, 11, 8, 0, 1)
            await self.processor.protocol_message(source, 112)

        dhw, heat, daily_dhw, daily_heat, daily = targets
        self.assertEqual(self.processor.value_store[dhw]["val"], 3)
        self.assertEqual(self.processor.value_store[heat]["val"], 9)
        self.assertEqual(self.processor.value_store[daily_dhw]["val"], 3)
        self.assertEqual(self.processor.value_store[daily_heat]["val"], 3)
        self.assertEqual(self.processor.value_store[daily]["val"], 6)
        self.processor._logger.error.assert_not_called()

    def test_mode_history_is_retained_until_every_counter_passes_the_switch(self):
        first_switch = (datetime(2026, 11, 7, 13, 0, 20), False, True)
        second_switch = (datetime(2026, 11, 7, 13, 0, 40), True, False)
        self.processor._mode_history = [first_switch, second_switch]
        self.processor.dhw_power_store = {"val": "OFF", "dt": "2026-11-07T13:00:40"}
        self.processor.value_store = {
            key: {"val": 100, "dt": "2026-11-07T13:00:00"} for key in self.delta_sources
        }
        self.processor.value_store["ENUM_IN_FSV_3011"] = {"val": "Yes"}
        for source in ("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM", "LVAR_IN_TOTAL_GENERATED_POWER"):
            self.processor.value_store[source]["dt"] = "2026-11-07T13:01:00"
            self.processor._prune_mode_history()
            self.assertEqual(self.processor._mode_history, [first_switch, second_switch])

        self.processor.value_store["LVAR_IN_MINUTES_ACTIVE"]["dt"] = "2026-11-07T13:00:30"
        self.processor._prune_mode_history()

        self.assertEqual(self.processor._mode_history, [second_switch])
        durations = self.processor._mode_durations(datetime(2026, 11, 7, 13, 0, 30), datetime(2026, 11, 7, 13, 1))
        self.assertEqual(durations, {True: 10, False: 20})
        self.processor.value_store["LVAR_IN_MINUTES_ACTIVE"]["dt"] = "2026-11-07T13:01:00"
        self.processor._prune_mode_history()
        self.assertEqual(self.processor._mode_history, [])

    async def test_runtime_resumes_after_reset_baseline_without_losing_later_deltas(self):
        self.restore_mode_totals()
        source = "LVAR_IN_MINUTES_ACTIVE"
        self.processor.value_store[source] = {"val": 100, "dt": "2026-11-07T13:09:19"}
        self.processor.dhw_power_store = {"val": "ON", "dt": "2026-11-07T13:00:00"}

        await self.processor.development_tool("reset_all_counters")
        await self.send_delta(source, 112)

        for key in self.delta_sources[source]:
            self.assertEqual(self.processor.value_store[key]["val"], 0)
        self.dt = "2026-11-07T13:11:19"
        await self.send_delta(source, 114)

        dhw, heat, daily_dhw, daily_heat, daily = self.delta_sources[source]
        for key in (dhw, daily_dhw, daily):
            self.assertEqual(self.processor.value_store[key]["val"], 2)
        for key in (heat, daily_heat):
            self.assertEqual(self.processor.value_store[key]["val"], 0)

    async def test_real_protocol_keeps_orphaned_totals_and_cop_unavailable(self):
        self.restore_mode_totals(missing_key="NASA_EHSSENTINEL_TOTAL_CONSUMED_POWER_HEAT_MODE")
        self.enable_protocol_processing()

        await self.processor.protocol_message("NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT_ACCUM", 10583)
        await self.processor.protocol_message("LVAR_IN_TOTAL_GENERATED_POWER", 32865)

        self.assertEqual(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_GENERATED_POWER_HEAT_MODE"]["val"], 300)
        self.assertIsNone(self.processor.value_store["NASA_EHSSENTINEL_TOTAL_COP_HEAT_MODE"]["val"])
        self.processor._logger.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()