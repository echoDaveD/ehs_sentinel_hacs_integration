"""Real Home Assistant restore test; run on Linux with HA 2026.10.4."""

import importlib.util
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


@unittest.skipUnless(
    sys.platform == "linux" and importlib.util.find_spec("homeassistant") is not None,
    "Requires Linux, Python 3.14, and homeassistant==2026.10.4",
)
class HomeAssistantSensorRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.instances = []

    async def asyncTearDown(self):
        for hass in reversed(self.instances):
            await hass.async_stop(force=True)

    async def create_sensor(self, value, seen_once):
        from homeassistant.components import sensor
        from homeassistant import loader
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import entity_registry
        from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
        from homeassistant.setup import async_setup_component

        from custom_components.ehs_sentinel.sensor import EHSSentinelSensor

        hass = HomeAssistant(self.directory.name)
        self.instances.append(hass)
        hass.config.skip_pip = True
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        self.assertTrue(await async_setup_component(hass, "sensor", {}))
        await hass.async_start()
        coordinator = DataUpdateCoordinator(hass, logging.getLogger(__name__), name="restore test", config_entry=None)
        coordinator.config_entry = SimpleNamespace(entry_id="restore_test")
        key = "outdoorcontrolwattmeterallunit"
        nasa_name = "NASA_OUTDOOR_CONTROL_WATTMETER_ALL_UNIT"
        coordinator.data = {"sensor": {key: {"value": value, "seen_once": seen_once}}}
        coordinator.nasa_repo = {nasa_name: {"hass_opts": {"unit": "kW", "device_class": "power"}}}
        coordinator.uses_legacy_naming = lambda: False
        coordinator.device_info = lambda: {"identifiers": {("ehs_sentinel", "restore_test")}, "name": "Restore Test"}
        entity = EHSSentinelSensor(coordinator, key, nasa_name)
        entity._attr_entity_registry_enabled_default = True
        registry = entity_registry.async_get(hass)
        registry.async_get_or_create(
            "sensor", "sensor", entity.unique_id, suggested_object_id="restore_test_power", disabled_by=None,
        )
        component = hass.data[sensor.DATA_COMPONENT]
        await component.async_add_entities([entity])
        await hass.async_block_till_done()
        return hass, entity, registry

    async def test_native_power_survives_real_storage_and_new_ha_instance_with_watt_display(self):
        from homeassistant.helpers import restore_state

        first, entity, registry = await self.create_sensor(1.29, True)
        entity_id = entity.entity_id
        registry.async_update_entity_options(entity_id, "sensor", {"unit_of_measurement": "W"})
        await first.async_block_till_done()
        state = first.states.get(entity_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.attributes["unit_of_measurement"], "W")
        self.assertEqual(float(state.state), 1290)
        await restore_state.async_get(first).async_dump_states()
        saved = json.loads((Path(self.directory.name) / ".storage/core.restore_state").read_text(encoding="utf-8"))
        stored = next(item for item in saved["data"] if item["state"]["entity_id"] == entity_id)
        self.assertEqual(float(stored["state"]["state"]), 1290)
        self.assertEqual(stored["extra_data"]["native_value"], 1.29)
        self.assertEqual(stored["extra_data"]["native_unit_of_measurement"], "kW")

        await first.async_stop()
        self.instances.remove(first)
        second, restored, registry = await self.create_sensor(None, False)

        self.assertIsNot(first, second)
        self.assertEqual(restored.entity_id, entity_id)
        self.assertEqual(registry.async_get(entity_id).options["sensor"]["unit_of_measurement"], "W")
        self.assertEqual(restored.native_value, 1.29)
        self.assertEqual(restored.native_unit_of_measurement, "kW")
        self.assertTrue(restored.available)
        state = second.states.get(entity_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.attributes["unit_of_measurement"], "W")
        self.assertEqual(float(state.state), 1290)


if __name__ == "__main__":
    unittest.main()