from .const import DOMAIN
from .start import start_ehs_sentinel
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry):
    config = entry.data
    hass.async_create_task(start_ehs_sentinel(hass, config))
    return True
