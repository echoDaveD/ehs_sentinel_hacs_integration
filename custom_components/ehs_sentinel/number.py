from homeassistant.components.number import NumberEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN, DEVICE_ID, PLATFORM_NUMBER

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_entity_adder(PLATFORM_NUMBER, async_add_entities)
    await coordinator.async_config_entry_first_refresh()
    entities = []
    for key, value in coordinator.data.get(PLATFORM_NUMBER, {}).items():
        entity = EHSSentinelNumber(coordinator, key, nasa_name=value.get('nasa_name', ))
        entities.append(entity)
        coordinator.data[PLATFORM_NUMBER][key]['_entity'] = entity  # speichere die entity im coordinator.data
    async_add_entities(entities)

class EHSSentinelNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    def update_value(self, valuedict):
        value = valuedict.get('value')
        nasa_name = valuedict.get('nasa_name')
        nasa_last_seen = valuedict.get('nasa_last_seen')
        seen_once = valuedict.get('seen_once', True)
        old_value = self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key, {}).get('value')
        old_nasa_name = self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key, {}).get('nasa_name', None)
        old_nasa_seen = self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key, {}).get('nasa_last_seen', None)
        old_seen_once = self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key, {}).get('seen_once', False)
        if old_value != value or old_nasa_name != nasa_name or old_nasa_seen != nasa_last_seen or old_seen_once != seen_once:
            self.coordinator.data[PLATFORM_NUMBER][self._key]['value'] = value
            self.coordinator.data[PLATFORM_NUMBER][self._key]['nasa_name'] = nasa_name
            self.coordinator.data[PLATFORM_NUMBER][self._key]['nasa_last_seen'] = nasa_last_seen
            self.coordinator.data[PLATFORM_NUMBER][self._key]['seen_once'] = seen_once
            if self.hass:
                self.async_write_ha_state()

    def __init__(self, coordinator, key, nasa_name=None):
        super().__init__(coordinator)
        self._key = key
        self._nasa_name= nasa_name
        hass_opts = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {})
        platform_opts = hass_opts.get('platform', {})
        self._device_class = hass_opts.get("device_class", None)
        self._state_class = hass_opts.get("state_class", None)
        self._unit = hass_opts.get("unit", None)
        self._mode = platform_opts.get("mode", None)
        self._min = platform_opts.get("min", None)
        self._max = platform_opts.get("max", None)
        self._step = platform_opts.get("step", None)
        self._attr_entity_registry_enabled_default = hass_opts.get("entity_registry_enabled_default", True)
        self._attr_entity_registry_visible_default = hass_opts.get("entity_registry_visible_default", True)
        entry = coordinator.config_entry
        
        # Kompatibilität zu alten Einträgen sicherstellen: Legacy-Schema nutzt die alte Unique-ID-Konvention.
        if coordinator.uses_legacy_naming():
            self._attr_name = f"{key}"
            self._attr_unique_id = f"{DEVICE_ID}{key.lower()}"
        else:
            self._attr_name = key
            self._attr_unique_id = (
                f"{coordinator.config_entry.entry_id}_{key.lower()}"
            )
        self._attr_has_entity_name = True
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            # Schreibe den wiederhergestellten Wert in den Coordinator, damit er sofort verfügbar ist
            platform_data = self.coordinator.data.setdefault(PLATFORM_NUMBER, {})
            platform_data.setdefault(self._key, {})

             # Konvertiere zu float/int und prüfe min/max
            try:
                state_val = float(last_state.state)
                if self._min is not None and state_val < self._min:
                    state_val = self._min
                if self._max is not None and state_val > self._max:
                    state_val = self._max
            except ValueError:
                return
            
            platform_data[self._key].update({
                "value": state_val,
                "nasa_name": self._nasa_name,
                "seen_once": True,
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
    def native_value(self):
        return self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key).get("value")

    @property
    def available(self):
        data = self.coordinator.data.get(PLATFORM_NUMBER, {}).get(self._key, {})
        return bool(data.get("seen_once", False))
    
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