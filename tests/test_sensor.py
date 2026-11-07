"""Sensor restoration tests runnable without importing Home Assistant."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


SENSOR_PATH = Path(__file__).resolve().parents[1] / "custom_components/ehs_sentinel/sensor.py"


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        pass


class SensorEntity:
    pass


class RestoreEntity:
    pass


class RestoreSensor(SensorEntity, RestoreEntity):
    pass


class SensorRestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(SENSOR_PATH.read_text(encoding="utf-8"))
        sensor_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                            and node.name == "EHSSentinelSensor")
        namespace = {
            "CoordinatorEntity": CoordinatorEntity,
            "SensorEntity": SensorEntity,
            "RestoreEntity": RestoreEntity,
            "RestoreSensor": RestoreSensor,
            "PLATFORM_SENSOR": "sensor",
            "DEVICE_ID": "test",
        }
        exec(compile(ast.Module(body=[sensor_class], type_ignores=[]), str(SENSOR_PATH), "exec"), namespace)
        self.key = "outdoorcontrolwattmeterallunit"
        self.nasa_name = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT"
        self.coordinator = SimpleNamespace(
            data={"sensor": {self.key: {"seen_once": False, "_entity_add_pending": True}}},
            nasa_repo={self.nasa_name: {"hass_opts": {"unit": "kW"}}},
            config_entry=SimpleNamespace(entry_id="test"),
            uses_legacy_naming=lambda: False,
        )
        self.sensor = namespace["EHSSentinelSensor"](self.coordinator, self.key, self.nasa_name)
        self.sensor.async_write_ha_state = Mock()
        self.sensor.async_get_last_state = AsyncMock(return_value=SimpleNamespace(
            state="1290",
            attributes={"unit_of_measurement": "W", "nasa_last_seen": "2026-11-07T10:00:00"},
        ))
        self.sensor.async_get_last_sensor_data = AsyncMock(return_value=SimpleNamespace(
            native_value=1.29, native_unit_of_measurement="kW",
        ))

    async def test_restores_native_value_when_display_unit_differs(self):
        self.assertIsInstance(self.sensor, RestoreSensor)
        await self.sensor.async_added_to_hass()

        self.assertEqual(self.sensor.native_value, 1.29)
        self.assertEqual(self.sensor.native_unit_of_measurement, "kW")
        self.assertTrue(self.sensor.available)
        self.assertEqual(self.sensor.extra_state_attributes["nasa_last_seen"], "2026-11-07T10:00:00")
        self.assertNotIn("_entity_add_pending", self.coordinator.data["sensor"][self.key])
        self.sensor.async_write_ha_state.assert_called_once()

    async def test_missing_native_data_does_not_restore_display_state(self):
        self.sensor.async_get_last_sensor_data.return_value = None

        await self.sensor.async_added_to_hass()

        self.assertIsNone(self.sensor.native_value)
        self.assertFalse(self.sensor.available)
        self.sensor.async_write_ha_state.assert_not_called()

    async def test_native_data_can_be_restored_without_display_state(self):
        self.sensor.async_get_last_state.return_value = None

        await self.sensor.async_added_to_hass()

        self.assertEqual(self.sensor.native_value, 1.29)
        self.assertTrue(self.sensor.available)
        self.sensor.async_write_ha_state.assert_called_once()

    async def test_native_values_are_preserved_without_string_conversion(self):
        for native_value in (0, -1.29, "ON", "0012", None):
            with self.subTest(native_value=native_value):
                self.coordinator.data["sensor"][self.key]["seen_once"] = False
                self.sensor.async_get_last_sensor_data.return_value.native_value = native_value

                await self.sensor.async_added_to_hass()

                self.assertEqual(self.sensor.native_value, native_value)

    async def test_live_packet_value_is_not_overwritten(self):
        self.coordinator.data["sensor"][self.key].update({"seen_once": True, "value": 1.28})

        await self.sensor.async_added_to_hass()

        self.assertEqual(self.sensor.native_value, 1.28)
        self.sensor.async_get_last_sensor_data.assert_not_awaited()
        self.sensor.async_get_last_state.assert_not_awaited()
        self.sensor.async_write_ha_state.assert_not_called()

    async def test_temperature_and_energy_restore_ignore_display_unit_conversion(self):
        cases = (("\u00b0C", "\u00b0F", 20, "68"), ("kWh", "Wh", 12.5, "12500"))
        for native_unit, display_unit, native_value, display_value in cases:
            with self.subTest(native_unit=native_unit):
                self.coordinator.data["sensor"][self.key]["seen_once"] = False
                self.sensor._unit = native_unit
                self.sensor.async_get_last_state.return_value.state = display_value
                self.sensor.async_get_last_state.return_value.attributes["unit_of_measurement"] = display_unit
                self.sensor.async_get_last_sensor_data.return_value.native_value = native_value
                self.sensor.async_get_last_sensor_data.return_value.native_unit_of_measurement = native_unit

                await self.sensor.async_added_to_hass()

                self.assertEqual(self.sensor.native_value, native_value)
                self.assertEqual(self.sensor.native_unit_of_measurement, native_unit)

    async def test_live_value_arriving_during_restore_is_not_overwritten(self):
        saved = self.sensor.async_get_last_sensor_data.return_value

        async def restore_with_live_packet():
            self.coordinator.data["sensor"][self.key].update({"seen_once": True, "value": 1.28})
            return saved

        self.sensor.async_get_last_sensor_data.side_effect = restore_with_live_packet

        await self.sensor.async_added_to_hass()

        self.assertEqual(self.sensor.native_value, 1.28)

    def test_live_update_preserves_value_metadata_and_availability(self):
        self.sensor.hass = SimpleNamespace()

        self.sensor.update_value({"value": 1.28, "nasa_name": self.nasa_name,
                                  "nasa_last_seen": "2026-11-07T13:00:00", "seen_once": True})

        self.assertEqual(self.sensor.native_value, 1.28)
        self.assertTrue(self.sensor.available)
        self.assertEqual(self.sensor.extra_state_attributes["nasa_last_seen"], "2026-11-07T13:00:00")
        self.sensor.async_write_ha_state.assert_called_once()

    def test_unchanged_live_update_does_not_write_state_again(self):
        self.sensor.hass = SimpleNamespace()
        payload = {"value": 1.28, "nasa_name": self.nasa_name,
                   "nasa_last_seen": "2026-11-07T13:00:00", "seen_once": True}
        self.sensor.update_value(payload)
        self.sensor.async_write_ha_state.reset_mock()

        self.sensor.update_value(payload)

        self.sensor.async_write_ha_state.assert_not_called()

    async def test_display_attributes_cannot_override_native_data(self):
        self.sensor.async_get_last_state.return_value.attributes.update({
            "value": 1290, "seen_once": False, "nasa_name": "other",
        })

        await self.sensor.async_added_to_hass()

        self.assertEqual(self.sensor.native_value, 1.29)
        self.assertTrue(self.sensor.available)
        self.assertEqual(self.sensor.extra_state_attributes["nasa_name"], self.nasa_name)


if __name__ == "__main__":
    unittest.main()