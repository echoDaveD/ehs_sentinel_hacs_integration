import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .coordinator import EHSSentinelCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up EHS Sentinel from a config entry."""
    coordinator = EHSSentinelCoordinator(hass, entry.data["ip"], entry.data["port"])
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})["coordinator"] = coordinator

    hass.async_create_task(coordinator.start_ehs_sentinel())

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.data[DOMAIN]["coordinator"].stop()
    return True