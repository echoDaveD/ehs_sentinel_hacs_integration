from homeassistant.components.sensor import SensorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN]["coordinator"]
    # Entitäten werden dynamisch im MessageProcessor angelegt
    # Hier könnte man weitere statische Sensoren hinzufügen, falls gewünscht