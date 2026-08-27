import logging
import os
import yaml
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from .coordinator import EHSSentinelCoordinator
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.exceptions import ServiceValidationError
from .const import DOMAIN, CONF_NAMING_SCHEME, LEGACY_INSTANCE_NAME, NAMING_SCHEME_LEGACY
from .nasa_packet import AddressClassEnum
from pathlib import Path

_LOGGER = logging.getLogger(__name__)
NASA_REPOSITORY_FILE = os.path.join(
    os.path.dirname(__file__), "data", "nasa_repository.yml"
)
NASA_REPOSITORY_FILE = os.path.abspath(NASA_REPOSITORY_FILE)
PLATFORMS = ["sensor", "number", "switch", "binary_sensor", "select"]

def get_entry_option(entry, key, default=None):
    return entry.options.get(key, entry.data.get(key, default))


def _resolve_coordinator_from_call(call: ServiceCall):
    """Resolve the target coordinator for service calls in multi-device setups."""
    coordinators = list(call.hass.data.get(DOMAIN, {}).values())
    if not coordinators:
        raise ServiceValidationError(
            translation_key="coordinator_not_found",
            translation_domain=DOMAIN,
        )

    device_id = call.data.get("device_id")
    if device_id:
        device_reg = dr.async_get(call.hass)
        device_entry = device_reg.async_get(device_id)
        if device_entry is None:
            raise ServiceValidationError(
                translation_key="unknown_device",
                translation_domain=DOMAIN,
                translation_placeholders={
                    "device_id": device_id,
                },
            )

        for coordinator in coordinators:
            if coordinator.config_entry.entry_id in device_entry.config_entries:
                return coordinator

        raise ServiceValidationError(
            translation_key="device_not_managed_by_integration",
            translation_domain=DOMAIN,
            translation_placeholders={
                "device_name": device_entry.name_by_user or device_entry.name or device_id,
            },
        )

    if len(coordinators) == 1:
        return coordinators[0]

    known_instances = [c.instance_name for c in coordinators]
    raise ServiceValidationError(
        translation_key="multiple_instances_require_instance",
        translation_domain=DOMAIN,
        translation_placeholders={
            "available_instances": ", ".join(known_instances),
        },
    )

async def async_migrate_entry(hass, config_entry):
    """No version bump; keep v1 for legacy unique-id behavior."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up EHS Sentinel from a config entry."""

    # Bestehende Eintraege ohne explizites Schema werden als legacy behandelt.
    if not entry.data.get(CONF_NAMING_SCHEME):
        patched_data = {**entry.data, CONF_NAMING_SCHEME: NAMING_SCHEME_LEGACY}

        # Legacy-Verhalten: Kein Name gesetzt, Fallback auf "Samsung EHSSentinel".
        if not entry.data.get("name"):
            patched_data["name"] = LEGACY_INSTANCE_NAME

        hass.config_entries.async_update_entry(entry, data=patched_data)

    _LOGGER.info(f"Setting up EHS Sentinel Instance: {get_entry_option(entry, 'name')} with IP: {get_entry_option(entry, 'ip')} and Port: {get_entry_option(entry, 'port')}")

    _LOGGER.debug(f"Loading NASA Repository from {NASA_REPOSITORY_FILE}")
    nasa_repo = await _load_nasa_repo(hass)
    nasa_keys = [k for k in nasa_repo.keys() if isinstance(nasa_repo[k], dict) and "address" in nasa_repo[k]]
    _LOGGER.debug("NASA Repository loaded")

    config_dict = {
        "name": get_entry_option(entry, "name"),
        "ip": get_entry_option(entry, "ip"),
        "port": get_entry_option(entry, "port"),
        "polling": get_entry_option(entry, "polling", False),
        "polling_yaml": get_entry_option(entry, "polling_yaml", ""),
        "write_mode": get_entry_option(entry, "write_mode", False),
        "extended_logging": get_entry_option(entry, "extended_logging", False),
        "force_refresh": get_entry_option(entry, "force_refresh", False),
        "diagnostic_logs": get_entry_option(entry, "diagnostic_logs", False),
    }
    _LOGGER.debug(f"Config Dict: {config_dict}")

    coordinator = EHSSentinelCoordinator(hass, entry, config_dict, nasa_repo)
    
    await coordinator.async_config_entry_first_refresh()

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    hass.async_create_task(coordinator.start_ehs_sentinel())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if get_entry_option(entry, CONF_NAMING_SCHEME, NAMING_SCHEME_LEGACY) != NAMING_SCHEME_LEGACY:
        await _enable_registry_entities_for_entry(hass, entry)

    hass.services.async_register(
        DOMAIN,
        "send_message",
        async_send_signal_service,
        schema=vol.Schema({
            vol.Optional("device_id"): cv.string,
            vol.Required("nasa_key"): vol.Any(vol.In(nasa_keys), vol.All(list, [vol.In(nasa_keys)])),
            vol.Required("nasa_value"): vol.Any(cv.string, vol.All(list, [cv.string]), None),
            vol.Optional("source_address_class"): cv.string,
            vol.Optional("source_address"): cv.positive_int,
            vol.Optional("source_channel"): cv.positive_int,
            vol.Optional("destination_address_class"): cv.string,
            vol.Optional("destination_address"): cv.positive_int,
            vol.Optional("destination_channel"): cv.positive_int,
            vol.Optional("packet_type"): cv.string,
            vol.Optional("data_type"): cv.string,
            }),
    )

    hass.services.async_register(
        DOMAIN,
        "request_message",
        async_request_signal_service,
        schema=vol.Schema({
            vol.Optional("device_id"): cv.string,
            vol.Required("nasa_key"): vol.In(nasa_keys)
        }),
    )

    hass.services.async_register(
        DOMAIN,
        "request_diagnostic_logs",
        async_request_current_diagnostics,
        schema=vol.Schema({
            vol.Optional("device_id"): cv.string,
        }),
    )

    hass.services.async_register(
        DOMAIN,
        "development_tools",
        async_development_tools_service,
        schema=vol.Schema({
            vol.Optional("device_id"): cv.string,
            vol.Required("tool_name"): cv.string
        }),
    )

    hass.services.async_register(
        DOMAIN,
        "export_fsv_file",
        async_export_fsv_file_service,
        schema=vol.Schema({
            vol.Optional("device_id"): cv.string,
            vol.Required("file_name"): cv.string
        }),
        supports_response=SupportsResponse.ONLY
    )

    hass.services.async_register(
        DOMAIN,
        "import_fsv_file",
        async_import_fsv_file_service,
        schema=vol.Schema({
            vol.Optional("device_id"): cv.string,
            vol.Required("file_name"): cv.string
        }),
        supports_response=SupportsResponse.ONLY
    )

    return True


async def _enable_registry_entities_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-enable entities previously disabled by integration for this entry."""
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    re_enabled = 0
    for entity_entry in entities:
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            ent_reg.async_update_entity(entity_entry.entity_id, disabled_by=None)
            re_enabled += 1

    if re_enabled:
        _LOGGER.info("[%s] Re-enabled %s entities for config entry %s", entry.title, re_enabled, entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.info(f"[{entry.title}] EHS Sentinel shutdown initiated for entry: {entry.entry_id}")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].get(entry.entry_id)
        if coordinator:
            await coordinator.stop()

        # Entity-Registry nicht loeschen: statische Entities sollen zwischen Reloads bestehen bleiben.
        hass.data[DOMAIN].pop(entry.entry_id, None)

    _LOGGER.info(f"[{entry.title}]EHS Sentinel shutdown successful completed for entry: {entry.entry_id}")

    return unload_ok

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
    keys = call.data.get("nasa_key")
    values = call.data.get("nasa_value")
    source_address_class = call.data.get("source_address_class", None)
    source_address = call.data.get("source_address", None)
    source_channel = call.data.get("source_channel", None)
    destination_address_class = call.data.get("destination_address_class", None)
    destination_address = call.data.get("destination_address", None)
    destination_channel = call.data.get("destination_channel", None)
    packet_type = call.data.get("packet_type", None)
    data_type = call.data.get("data_type", None)

    coordinator = _resolve_coordinator_from_call(call)


    _LOGGER.info(f"Service Action Call: Send Message for {keys} with Value {values}")

    await coordinator.producer.write_request(
        message=keys,
        value=values,
        read_request_after=True,
        source_address_class=source_address_class,
        source_address=source_address,
        source_channel=source_channel,
        dest_address_class=destination_address_class,
        dest_address=destination_address,
        dest_channel=destination_channel,
        packet_type=packet_type,
        data_type=data_type
    )

async def async_request_signal_service(call: ServiceCall):
    key = call.data.get("nasa_key")
    coordinator = _resolve_coordinator_from_call(call)
    
    _LOGGER.info(f"Service Action Call: Request Message {key}")

    await coordinator.producer.read_request(
        list_of_messages=[key],
        retry_mode=True
    )

async def async_request_current_diagnostics(call: ServiceCall):
    coordinator = _resolve_coordinator_from_call(call)
    _LOGGER.info(f"Service Action Call: Request current Diagnostics")
    await coordinator._log_task_stats()

async def async_development_tools_service(call: ServiceCall):
    tool_name = call.data.get("tool_name")
    coordinator = _resolve_coordinator_from_call(call)
    
    _LOGGER.info(f"Service Action Call: Development Tool {tool_name}")
    
    await coordinator.processor.development_tool(tool_name)

async def async_export_fsv_file_service(call: ServiceCall):
    file_name = call.data.get("file_name")
    coordinator = _resolve_coordinator_from_call(call)
    log_dir = Path(
            coordinator.hass.config.path("www", DOMAIN, "logs")
        )
    _LOGGER.info(f"Service Action Call: Export FSV File {log_dir / file_name}")

    dict_to_export = {}

    for category in coordinator.data.values():
        for entity, value in category.items():
            if "_FSV_" in value['nasa_name']:
                dict_to_export[value['nasa_name']] = value['value']

    def _write_yaml(path, data):
        with open(path, "w") as file:
            yaml.dump(data, file)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        await coordinator.hass.async_add_executor_job(_write_yaml, log_dir / file_name, dict_to_export)
        _LOGGER.info(f"FSV File {log_dir / file_name} exported successfully.")    
        return {"status": "success", "message": f"Exported FSV File {log_dir / file_name}"}
    except Exception as e:
        _LOGGER.error(f"Error exporting FSV File {log_dir / file_name}: {e}")
        return {"status": "error", "message": f"Error exporting FSV File {log_dir / file_name}: {e}"}

async def async_import_fsv_file_service(call: ServiceCall) -> ServiceResponse:
    file_name = call.data.get("file_name")
    coordinator = _resolve_coordinator_from_call(call)
   
    log_dir = Path(
            coordinator.hass.config.path("www", DOMAIN, "logs")
        )
    _LOGGER.info(f"Service Action Call: Import FSV File {log_dir / file_name}")
    if os.path.isfile(log_dir / file_name):
        def _read_yaml(path):
            with open(path, "r") as file:
                return yaml.safe_load(file)

        data = await coordinator.hass.async_add_executor_job(_read_yaml, log_dir / file_name)
        
        current_values = {
            value['nasa_name']: value['value'] for category in coordinator.data.values() for entity, value in category.items() if "_FSV_" in value['nasa_name']
        }
        dict_to_update = {}
        for key, value in data.items():
            if key not in current_values or current_values[key] != value:
                dict_to_update[key] = {"old_value": current_values.get(key), "new_value": value}

        for key, value in dict_to_update.items():

            if type(value['new_value']) == bool:
                if value['new_value'] == True:
                    value['new_value'] = "ON"
                else:
                    value['new_value'] = "OFF"
            
            _LOGGER.info(f"FSV Key {key} from file {log_dir / file_name} is different from current value. Current: {value['old_value']}, Backup: {value['new_value']}. Restoring value.")
            
            await coordinator.producer.write_request(
                message=key,
                value=f"{value['new_value']}",
                read_request_after=True
            )
        return {"status": "success", "message": f"Imported FSV File {log_dir / file_name}", "updated_entities": dict_to_update}
    else:
        _LOGGER.error(f"FSV File {log_dir / file_name} not found.")
        return {"status": "error", "message": f"FSV File {log_dir / file_name} not found."}
    