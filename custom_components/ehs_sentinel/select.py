from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, DEVICE_ID, PLATFORM_SELECT

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_entity_adder(PLATFORM_SELECT, async_add_entities)
    await coordinator.async_config_entry_first_refresh()
    entities = []
    for key, value in coordinator.data.get(PLATFORM_SELECT, {}).items():
        entities.append(EHSSentinelSelect(coordinator, key, nasa_name=value.get('nasa_name', )))
    async_add_entities(entities)

class EHSSentinelSelect(CoordinatorEntity, SelectEntity):

    def __init__(self, coordinator, key, nasa_name=None):
        super().__init__(coordinator)
        self._key = key
        self._nasa_name = nasa_name
        self._device_class = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("device_class", None)
        self._state_class = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("state_class", None)
        self._unit = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("unit", None)
        self._options = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get('platform', {}).get("options", [])
        self._attr_name = f"{key}"
        self._attr_unique_id = f"{DEVICE_ID}{key.lower()}"
        self._attr_has_entity_name = True
        self.coordinator = coordinator

    @property
    def device_info(self):
        return self.coordinator.device_info()

    @property
    def device_class(self):
        return self._device_class

    @property
    def state_class(self):
        return self._state_class

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def current_option(self):
        return self.coordinator.data.get(PLATFORM_SELECT, {}).get(self._key).get("value")

    @property
    def options(self):
        return self._options
    
    @property
    def extra_state_attributes(self):
        attrs = {}
        if self._nasa_name:
            attrs["nasa_name"] = self._nasa_name
        return attrs

    async def async_select_option(self, option: str):
        # Hier Option setzen (z.B. an Gerät senden)
        await self.coordinator.producer.write_request(message=self._nasa_name, value=f"{option}", read_request_after=True)
        # Optional: Wert lokal setzen, falls das Gerät nicht sofort zurückmeldet
        self.coordinator.data[PLATFORM_SELECT][self._key] = {"nasa_name": self._nasa_name, "value": option}
        self.async_write_ha_state()

