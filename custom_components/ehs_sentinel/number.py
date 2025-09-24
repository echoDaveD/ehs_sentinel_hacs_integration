from homeassistant.components.number import NumberEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import async_generate_entity_id
from .const import DOMAIN, DEVICE_ID, PLATFORM_NUMBER

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_entity_adder(PLATFORM_NUMBER, async_add_entities)
    await coordinator.async_config_entry_first_refresh()
    entities = []
    for key, value in coordinator.data.get(PLATFORM_NUMBER, {}).items():
        base_id = f"{DEVICE_ID.lower()}_{key.lower()}"
        entity_id = async_generate_entity_id(
            PLATFORM_NUMBER + ".{}",
            base_id,
            hass.states.async_entity_ids(PLATFORM_NUMBER)
        )
        entity = EHSSentinelNumber(coordinator, key, nasa_name=value.get('nasa_name', ))
        entity.entity_id = entity_id  # explizit hier setzen
        entities.append(entity)
    async_add_entities(entities)

class EHSSentinelNumber(CoordinatorEntity, NumberEntity):

    def __init__(self, coordinator, key, nasa_name=None):
        super().__init__(coordinator)
        self._key = key
        self._nasa_name= nasa_name
        self._device_class = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("device_class", None)
        self._state_class = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("state_class", None)
        self._unit = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("unit", None)
        self._mode = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get('platform', {}).get("mode", None)
        self._min = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get('platform', {}).get("min", None)
        self._max = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get('platform', {}).get("max", None)
        self._step = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get('platform', {}).get("step", None)
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
    def native_value(self):
        return self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key).get("value")
    
    @property
    def native_min_value(self) -> float:
        return self._min

    @property
    def native_max_value(self) -> float:
        return self._max

    @property
    def native_step(self) -> float:
        return self._step

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def native_unit_of_measurement(self):
        return self._unit
    
    @property
    def extra_state_attributes(self):
        attrs = {}
        data = self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key, {})
        if "nasa_last_seen" in data:
            attrs["nasa_last_seen"] = data["nasa_last_seen"]
        if self._nasa_name:
            attrs["nasa_name"] = self._nasa_name
        return attrs

    async def async_set_native_value(self, value: float):
        # Hier Wert setzen (z.B. an Gerät senden)
        await self.coordinator.producer.write_request(message=self._nasa_name, value=f"{value}", read_request_after=True)
        # Optional: Wert lokal setzen, falls das Gerät nicht sofort zurückmeldet
        #self.coordinator.data[PLATFORM_NUMBER][self._key] = {"nasa_name": self._nasa_name, "value": value}
        self.async_write_ha_state()