import logging
import os
import yaml
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from .coordinator import EHSSentinelCoordinator
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.exceptions import ServiceValidationError
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
    nasa_keys = [k for k in nasa_repo.keys() if isinstance(nasa_repo[k], dict) and "address" in nasa_repo[k]]
    _LOGGER.debug("NASA Repository loaded")
    coordinator = EHSSentinelCoordinator(hass, entry, nasa_repo)
    
    await coordinator.async_config_entry_first_refresh()
    
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    hass.async_create_task(coordinator.start_ehs_sentinel())

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "number", "switch", "binary_sensor", "select"])

    hass.services.async_register(
        DOMAIN,
        "send_message",
        async_send_signal_service,
        schema=vol.Schema({
            vol.Required("nasa_key"): vol.In(nasa_keys),
            vol.Required("nasa_value"): vol.Any(cv.string, None),
        }),
    )

    hass.services.async_register(
        DOMAIN,
        "request_message",
        async_request_signal_service,
        schema=vol.Schema({
            vol.Required("nasa_key"): vol.In(nasa_keys)
        }),
    )

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


async def async_send_signal_service(call: ServiceCall):
    key = call.data.get("nasa_key")
    value = call.data.get("nasa_value")
    coordinator = next(iter(call.hass.data[DOMAIN].values()))
    if not coordinator:
        raise ServiceValidationError(
                translation_key="coordinator_not_found",
                translation_domain=DOMAIN,
            )
    
    _LOGGER.info(f"Service Action Call: Send Message for {key} with Value {value}")

    await coordinator.producer.write_request(
        message=key,
        value=value,
        read_request_after=True
    )

async def async_request_signal_service(call: ServiceCall):
    key = call.data.get("nasa_key")
    coordinator = next(iter(call.hass.data[DOMAIN].values()))
    if not coordinator:
        raise ServiceValidationError(
                translation_key="coordinator_not_found",
                translation_domain=DOMAIN,
            )
    
    _LOGGER.info(f"Service Action Call: Request Message {key}")

    await coordinator.producer.read_request(
        list_of_messages=[key]
    )