from .const import DOMAIN
from .start import start_ehs_sentinel
import logging

_LOGGER = logging.getLogger(__name__)
from .start import start_ehs_sentinel

async def async_setup_entry(hass, config_entry):
    hass.data.setdefault(DOMAIN, {})
    hass.async_create_task(start_ehs_sentinel(hass, config_entry.data))
    return True