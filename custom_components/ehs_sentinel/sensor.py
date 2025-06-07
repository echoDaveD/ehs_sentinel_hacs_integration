from homeassistant.components.sensor import SensorEntity
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import NumberEntity
from .const import DOMAIN

async def async_setup_entry(hass, config_entry, async_add_entities):
    hass.data[DOMAIN]["sensor_add_cb"] = async_add_entities
    hass.data[DOMAIN].setdefault("entities", [])
    # keine Entitäten initial – alles dynamisch
    return True

class DynamicSensor(SensorEntity):
    def __init__(self, name, value):
        self._attr_name = name
        self._attr_native_value = value
        self._attr_unique_id = f"{name}_sensor".replace(" ", "_")

    async def async_update(self):
        pass

class DynamicBinarySensor(BinarySensorEntity):
    def __init__(self, name, state):
        self._attr_name = name
        self._attr_is_on = state
        self._attr_unique_id = f"{name}_binary".replace(" ", "_")

class DynamicNumber(NumberEntity):
    def __init__(self, name, value):
        self._attr_name = name
        self._attr_native_value = value
        self._attr_native_unit_of_measurement = "°C"
        self._attr_min_value = 10
        self._attr_max_value = 35
        self._attr_step = 0.5
        self._attr_mode = "slider"
        self._attr_unique_id = f"{name}_number".replace(" ", "_")

    async def async_set_native_value(self, value):
        self._attr_native_value = value
        self.async_write_ha_state()
