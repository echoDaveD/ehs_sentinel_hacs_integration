from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN, DEVICE_ID, PLATFORM_SELECT

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_entity_adder(PLATFORM_SELECT, async_add_entities)
    await coordinator.async_config_entry_first_refresh()
    entities = []
    for key, value in coordinator.data.get(PLATFORM_SELECT, {}).items():
        base_id = f"{DEVICE_ID.lower()}_{key.lower()}"
        entity_id = async_generate_entity_id(
            PLATFORM_SELECT + ".{}",
            base_id,
            hass.states.async_entity_ids(PLATFORM_SELECT)
        )
        entity = EHSSentinelSelect(coordinator, key, nasa_name=value.get('nasa_name', ))
        entity.entity_id = entity_id  # explizit hier setzen
        entities.append(entity)
    async_add_entities(entities)

class EHSSentinelSelect(CoordinatorEntity, SelectEntity, RestoreEntity):

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

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            # Schreibe den wiederhergestellten Wert in den Coordinator, damit er sofort verfügbar ist
            platform_data = self.coordinator.data.setdefault(PLATFORM_SELECT, {})
            platform_data.setdefault(self._key, {})

            # Stelle sicher dass der Wert in den erlaubten Options ist
            state_val = last_state.state
            if state_val in self._options:
                platform_data[self._key].update({
                    "value": state_val,
                    "nasa_name": self._nasa_name,
                    **last_state.attributes  #  alle Attribute wieder übernehmen
                })
                # sofort im UI zeigen
                self.async_write_ha_state()

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
        data = self.coordinator.data.get(PLATFORM_SELECT, {}).get(self._key, {})
        if "nasa_last_seen" in data:
            attrs["nasa_last_seen"] = data["nasa_last_seen"]
        if self._nasa_name:
            attrs["nasa_name"] = self._nasa_name
        return attrs

    async def async_select_option(self, option: str):
        # Hier Option setzen (z.B. an Gerät senden)
        await self.coordinator.producer.write_request(message=self._nasa_name, value=f"{option}", read_request_after=True)
        # Optional: Wert lokal setzen, falls das Gerät nicht sofort zurückmeldet
        #self.coordinator.data[PLATFORM_SELECT][self._key] = {"nasa_name": self._nasa_name, "value": option}
        self.async_write_ha_state()

