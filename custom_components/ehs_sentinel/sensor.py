from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN, DEVICE_ID, PLATFORM_SENSOR

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_entity_adder(PLATFORM_SENSOR, async_add_entities)
    await coordinator.async_config_entry_first_refresh()
    entities = []
    for key, value in coordinator.data.get(PLATFORM_SENSOR, {}).items():
        base_id = f"{DEVICE_ID.lower()}_{key.lower()}"
        entity_id = async_generate_entity_id(
            PLATFORM_SENSOR + ".{}",
            base_id,
            hass.states.async_entity_ids(PLATFORM_SENSOR)
        )
        entity = EHSSentinelSensor(coordinator, key, nasa_name=value.get('nasa_name', ))
        entity.entity_id = entity_id  # explizit hier setzen
        entities.append(entity)
    async_add_entities(entities)

class EHSSentinelSensor(CoordinatorEntity, SensorEntity, RestoreEntity):

    def __init__(self, coordinator, key, nasa_name=None):
        super().__init__(coordinator)
        self._key = key
        self._nasa_name = nasa_name
        self._device_class = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("device_class", None)
        self._state_class = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("state_class", None)
        self._unit = self.coordinator.nasa_repo.get(self._nasa_name, {}).get('hass_opts', {}).get("unit", None)
        self._attr_name = f"{key}"
        self._attr_unique_id = f"{DEVICE_ID}{key.lower()}"
        self._attr_has_entity_name = True
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            # Schreibe den wiederhergestellten Wert in den Coordinator, damit er sofort verfügbar ist
            platform_data = self.coordinator.data.setdefault(PLATFORM_SENSOR, {})
            platform_data.setdefault(self._key, {})

            state_val = last_state.state
            # sichere Konvertierung: "unknown", "unavailable" oder leere Werte -> None
            if state_val is None or (isinstance(state_val, str) and state_val.lower() in ("unknown", "unavailable", "")):
                conv = None
            else:
                # versuche Integer, dann Float, sonst fallback auf Original (z.B. Enum-String)
                try:
                    if isinstance(state_val, str) and state_val.isdigit():
                        conv = int(state_val)
                    else:
                        conv = float(state_val)
                except Exception:
                    conv = state_val

            platform_data[self._key].update({
                "value": conv,
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
    def native_value(self):
        return self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._key).get("value")
    
    @property
    def native_unit_of_measurement(self):
        return self._unit
    
    @property
    def extra_state_attributes(self):
        attrs = {}
        data = self.coordinator.data.get(PLATFORM_SENSOR, {}).get(self._key, {})
        if "nasa_last_seen" in data:
            attrs["nasa_last_seen"] = data["nasa_last_seen"]
        if self._nasa_name:
            attrs["nasa_name"] = self._nasa_name
        return attrs
