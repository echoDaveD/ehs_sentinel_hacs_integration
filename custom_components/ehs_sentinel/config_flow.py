from homeassistant import config_entries
from homeassistant.helpers.selector import selector
from homeassistant.helpers import device_registry
import voluptuous as vol
import asyncio
import yaml
from .const import DOMAIN, DEFAULT_POLLING_YAML, CONF_NAMING_SCHEME, NAMING_SCHEME_ENTRY_ID

CONFIG_SCHEMA = vol.Schema({
                    vol.Required("name", default="Heatpump MONO HT QUIET"): str,
                    vol.Required("ip", default="192.168.2.200"): str,
                    vol.Required("port", default=4196): int,
                    vol.Required("write_mode", default=True): bool,
                    vol.Required("polling", default=True): bool,
                    vol.Required("polling_yaml", default=DEFAULT_POLLING_YAML): selector({
                        "text": {
                            "multiline": True,
                            "multiple": False
                        }
                    }),
                    vol.Required("extended_logging", default=False): bool,
                    vol.Required("skip_mqtt_test", default=False): bool,
                    vol.Required("force_refresh", default=False): bool,
                    vol.Required("diagnostic_logs", default=False): bool,
                })

async def test_connection(ip, port) -> bool:
    try:
        reader, writer = await asyncio.open_connection(ip, port)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False
    
async def test_old_mqtt_device(hass) -> bool:
    
    devregistry = device_registry.async_get(hass)

    for device_id, device_entry in devregistry.devices.items():
        if any(len(identifier) == 2 and identifier[0] == "mqtt" and identifier[1] == "samsung_ehssentinel" for identifier in device_entry.identifiers):
            return False

    return True

class EHSSentinelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for EHS Sentinel."""

    VERSION = 2
    
    async def async_step_user(self, user_input=None):
        errors = {} 
        
        if user_input is not None:

            await self.async_set_unique_id(f"{user_input['ip']}:{user_input['port']}")
            self._abort_if_unique_id_configured()  

            ok = True

            if not user_input.get("skip_mqtt_test", False):
                ok = await test_old_mqtt_device(self.hass)

                if not ok:
                    errors["base"] = "old_mqtt_device"

            if ok:
                ok = await test_connection(user_input["ip"], user_input["port"])

                if not ok:
                    errors["base"] = "connection_failed"

            if len(errors) == 0:
                self.name = user_input["name"]
                self.ip = user_input["ip"]
                self.port = user_input["port"]
                self.polling = user_input["polling"]
                self.polling_yaml = user_input["polling_yaml"]
                self.write_mode = user_input["write_mode"]  
                self.extended_logging = user_input["extended_logging"] 
                self.force_refresh = user_input["force_refresh"]
                self.diagnostic_logs = user_input["diagnostic_logs"]

                return self.async_create_entry(
                    title=f"{self.name}",
                    data={
                        "name": self.name,
                        "ip": self.ip,
                        "port": self.port,
                        CONF_NAMING_SCHEME: NAMING_SCHEME_ENTRY_ID,
                        "polling": self.polling,
                        "polling_yaml": self.polling_yaml,
                        "write_mode": self.write_mode,
                        "extended_logging": self.extended_logging,
                        "polling_yaml": self.polling_yaml,
                        "force_refresh": self.force_refresh,
                        "diagnostic_logs": self.diagnostic_logs,
                    }
                )
            
        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_SCHEMA,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return EHSSentinelOptionsFlowHandler(config_entry)

class EHSSentinelOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._name = config_entry.data.get("name", "EHS Sentinel")
        self._ip = config_entry.options.get("ip", config_entry.data.get("ip"))
        self._port = config_entry.options.get("port", config_entry.data.get("port", 4196))
        self._polling_enabled = config_entry.options.get("polling", config_entry.data.get("polling", False))
        self._polling_yaml = config_entry.options.get("polling_yaml", config_entry.data.get("polling_yaml", DEFAULT_POLLING_YAML))
        self._write_mode = config_entry.options.get("write_mode", config_entry.data.get("write_mode", False))
        self._extended_logging = config_entry.options.get("extended_logging", config_entry.data.get("extended_logging", False))
        self._force_refresh = config_entry.options.get("force_refresh", config_entry.data.get("force_refresh", False))
        self._diagnostic_logs = config_entry.options.get("diagnostic_logs", config_entry.data.get("diagnostic_logs", False))

    def _is_host_port_in_use(self, ip: str, port: int) -> bool:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == self.config_entry.entry_id:
                continue
            other_ip = entry.options.get("ip", entry.data.get("ip"))
            other_port = entry.options.get("port", entry.data.get("port"))
            if other_ip == ip and other_port == port:
                return True
        return False

    async def async_step_init(self, user_input=None):
        errors = {}
        ip = self._ip
        port = self._port
        polling_yaml = self._polling_yaml
        extended_logging = self._extended_logging
        write_mode = self._write_mode
        polling_enabled = self._polling_enabled
        force_refresh = self._force_refresh
        diagnostic_logs = self._diagnostic_logs
        if user_input is not None:
            extended_logging = user_input.get("extended_logging", extended_logging)
            if user_input.get("reset_defaults"):
                polling_yaml = DEFAULT_POLLING_YAML
                write_mode = False
                polling_enabled = False
                force_refresh = False
                diagnostic_logs = False
            else:
                ip = user_input["ip"]
                port = user_input["port"]
                polling_yaml = user_input["polling_yaml"]
                write_mode = user_input["write_mode"]
                polling_enabled = user_input["polling"]
                force_refresh = user_input["force_refresh"]
                diagnostic_logs = user_input["diagnostic_logs"]
            # YAML validieren
            try:
                yaml.safe_load(polling_yaml)
            except Exception:
                errors["polling_yaml"] = "invalid_yaml"

            if not errors and self._is_host_port_in_use(ip, port):
                errors["base"] = "already_configured"

            if not errors:
                ok = await test_connection(ip, port)
                if not ok:
                    errors["base"] = "connection_failed"

            if not errors:
                new_data = {
                    **self.config_entry.data,
                    "ip": ip,
                    "port": port,
                    "name": self._name,
                    CONF_NAMING_SCHEME: self.config_entry.data.get(CONF_NAMING_SCHEME, NAMING_SCHEME_ENTRY_ID),
                }
                new_unique_id = f"{ip}:{port}"
                return await self._update_and_reload({
                        "ip": ip,
                        "port": port,
                        "polling": polling_enabled,
                        "polling_yaml": polling_yaml,
                        "write_mode": write_mode,
                        "extended_logging": extended_logging,
                        "force_refresh": force_refresh,
                        "diagnostic_logs": diagnostic_logs,
                    }, new_data, f"{self._name}", new_unique_id)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                    vol.Required("ip", default=ip): str,
                    vol.Required("port", default=port): int,
                    vol.Required("write_mode", default=write_mode): bool,
                    vol.Required("polling", default=polling_enabled): bool,
                    vol.Required("polling_yaml", default=polling_yaml): selector({
                        "text": {
                            "multiline": True,
                            "multiple": False
                        }
                    }),
                    vol.Required("extended_logging", default=extended_logging): bool,
                    vol.Required("force_refresh", default=force_refresh): bool,
                    vol.Required("diagnostic_logs", default=diagnostic_logs): bool,
                }),
            errors=errors,
        )
    
    async def _update_and_reload(self, new_options: dict, new_data: dict, title: str, unique_id: str):
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=new_data,
            options=new_options,
            unique_id=unique_id,
        )
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(title=title, data=new_options)
    