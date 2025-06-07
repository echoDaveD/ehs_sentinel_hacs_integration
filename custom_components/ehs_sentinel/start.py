import asyncio
#from .startEHSSentinel import main as ehs_main
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
import logging

_LOGGER = logging.getLogger(__name__)

async def start_ehs_sentinel(hass, config):
    _LOGGER.info("Starting EHS Sentinel with config: %s", config)
    try:
        #asyncio.create_task(ehs_main())
        pass
    except Exception as e:
        _LOGGER.error(f"Error starting EHS Sentinel: {e}")

async def async_setup_entry(hass, config_entry, async_add_entities):
    entities = [
        DemoSensor("Demo Temperatur", 22.5),
        DemoBinarySensor("Türkontakt", True),
        DemoNumber("Sollwert Temperatur", 21.0),
        DemoSelect("Betriebsmodus", ["Auto", "Manuell", "Eco"], "Auto")
    ]
    async_add_entities(entities)

class DemoSensor(SensorEntity):
    def __init__(self, name, value):
        self._attr_name = name
        self._attr_native_value = value
        self._attr_unique_id = f"{name}_sensor"

class DemoBinarySensor(BinarySensorEntity):
    def __init__(self, name, state):
        self._attr_name = name
        self._attr_is_on = state
        self._attr_unique_id = f"{name}_binary"

class DemoNumber(NumberEntity):
    def __init__(self, name, value):
        self._attr_name = name
        self._attr_native_value = value
        self._attr_native_unit_of_measurement = "°C"
        self._attr_min_value = 15.0
        self._attr_max_value = 30.0
        self._attr_step = 0.5
        self._attr_mode = "slider"
        self._attr_unique_id = f"{name}_number"

    async def async_set_native_value(self, value):
        self._attr_native_value = value
        self.async_write_ha_state()

class DemoSelect(SelectEntity):
    def __init__(self, name, options, current):
        self._attr_name = name
        self._attr_options = options
        self._attr_current_option = current
        self._attr_unique_id = f"{name}_select"

    async def async_select_option(self, option):
        self._attr_current_option = option
        self.async_write_ha_state()
