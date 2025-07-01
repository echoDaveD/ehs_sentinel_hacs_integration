import logging
import os
import yaml
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .coordinator import EHSSentinelCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
NASA_REPOSITORY_FILE = os.path.join(
    os.path.dirname(__file__), "data", "nasa_repository.yml"
)
NASA_REPOSITORY_FILE = os.path.abspath(NASA_REPOSITORY_FILE)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up EHS Sentinel from a config entry."""
    _LOGGER.info(f"Setting up EHS Sentinel with IP: {entry.data['ip']} and Port: {entry.data['port']}")

    _LOGGER.debug(f"Loading NASA Repository from {NASA_REPOSITORY_FILE}")
    nasa_repo = await _load_nasa_repo(hass)
    _LOGGER.debug("NASA Repository loaded")
    coordinator = EHSSentinelCoordinator(hass, entry, nasa_repo)
    
    await coordinator.async_config_entry_first_refresh()
    
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    hass.async_create_task(coordinator.start_ehs_sentinel())

    #await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "number", "switch", "binary_sensor", "select"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    await hass.data[DOMAIN][entry.entry_id].stop()
    return True

async def _load_nasa_repo(hass):
    try:
        if os.path.isfile(NASA_REPOSITORY_FILE):
            def _read_yaml():
                with open(NASA_REPOSITORY_FILE, mode='r') as file:
                    return yaml.safe_load(file)
            return await hass.async_add_executor_job(_read_yaml)
        else:
            raise Exception(f"{NASA_REPOSITORY_FILE} File not Found")
    except Exception as e:
        _LOGGER.error(f"Error while loading NASA Repository: {e}")
        return {}