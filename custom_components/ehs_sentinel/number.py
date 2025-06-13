from homeassistant.components.number import NumberEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN 

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_entity_adder("number", async_add_entities)
    await coordinator.async_config_entry_first_refresh()
    entities = []
    for key in coordinator.data.get("number", {}):
        entities.append(EHSSentinelNumber(coordinator, key))
    async_add_entities(entities)

class EHSSentinelNumber(CoordinatorEntity, NumberEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self._key = key
        self._device_class = self.coordinator.nasa_repo.get(self._key, {}).get('hass_opts', {}).get("device_class", None)
        self._state_class = self.coordinator.nasa_repo.get(self._key, {}).get('hass_opts', {}).get("state_class", None)
        self._unit = self.coordinator.nasa_repo.get(self._key, {}).get('hass_opts', {}).get("unit", None)
        self._attr_name = f"EHS Sentinel Number {key}"
        self._attr_unique_id = f"ehs_sentinel_number_{key}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def device_class(self):
        return self._device_class

    @property
    def state_class(self):
        return self._state_class

    @property
    def native_value(self):
        return self.coordinator.data.get("number", {}).get(self._key) in (True, "on", "ON", 1)
    
    @property
    def native_unit_of_measurement(self):
        return self._unit

    async def async_set_value(self, value: float):
        # Hier Wert setzen (z.B. an Gerät senden)
        pass