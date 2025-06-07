from .const import DOMAIN
from homeassistant.helpers.discovery import async_load_platform
from .start import start_ehs_sentinel
import logging

_LOGGER = logging.getLogger(__name__)
from .start import start_ehs_sentinel

async def async_setup_entry(hass, config_entry):
    hass.data.setdefault(DOMAIN, {})
     # Plattform „sensor“ explizit laden
    hass.async_create_task(
        async_load_platform(hass, "sensor", DOMAIN, {}, config_entry)
    )
    hass.async_create_task(start_ehs_sentinel(hass, config_entry.data))
    return True