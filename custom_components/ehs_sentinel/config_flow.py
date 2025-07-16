from homeassistant import config_entries
from homeassistant.helpers.selector import selector
import voluptuous as vol
import asyncio
import yaml
import os
from .const import DOMAIN, DEFAULT_POLLING_YAML

async def test_connection(ip, port):
    try:
        reader, writer = await asyncio.open_connection(ip, port)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

class EHSSentinelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for EHS Sentinel."""

    VERSION = 1
    
    async def async_step_user(self, user_input=None):
        errors = {}
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        
        if user_input is not None:
            ok = await test_connection(user_input["ip"], user_input["port"])
            if not ok:
                errors["base"] = "connection_failed"
            else:
                self.ip = user_input["ip"]
                self.port = user_input["port"]
                self.polling = user_input["polling"]
                self.write_mode = user_input["write_mode"]  
                self.extended_logging = user_input["extended_logging"] 
                if user_input["polling"]:
                    return await self.async_step_polling()
                else:
                    return self.async_create_entry(
                        title=f"{self.ip}",
                        data={
                            "ip": self.ip,
                            "port": self.port,
                            "polling": self.polling,
                            "write_mode": self.write_mode,
                            "extended_logging": self.extended_logging,
                            "polling_yaml": DEFAULT_POLLING_YAML
                        }
                    )
            
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("ip", default="192.168.2.200"): str,
                vol.Required("port", default=4196): int,
                vol.Required("polling", default=False): bool,
                vol.Required("write_mode", default=False): bool,
                vol.Required("extended_logging", default=False): bool,
            }),
            errors=errors,
        )

    async def async_step_polling(self, user_input=None):
        if user_input is not None:
            self.polling_yaml = user_input["polling_yaml"]
            return self.async_create_entry(
                title=f"{self.ip}",
                data={
                    "ip": self.ip,
                    "port": self.port,
                    "polling": self.polling,
                    "write_mode": self.write_mode,
                    "extended_logging": self.extended_logging,
                    "polling_yaml": self.polling_yaml
                }
            )
        return self.async_show_form(
            step_id="polling",
            data_schema=vol.Schema({
                vol.Required("polling_yaml", default=DEFAULT_POLLING_YAML): selector({
                    "text": {
                        "multiline": True,
                        "multiple": False
                    }
                }),
            }),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return EHSSentinelOptionsFlowHandler(config_entry)

class EHSSentinelOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._polling_enabled = config_entry.data.get("polling", False)
        self._polling_yaml = config_entry.options.get("polling_yaml", config_entry.data.get("polling_yaml", DEFAULT_POLLING_YAML))
        self._extended_logging = config_entry.options.get("extended_logging", config_entry.data.get("extended_logging", False))

    async def async_step_init(self, user_input=None):
        errors = {}
        polling_yaml = self._polling_yaml
        extended_logging = self._extended_logging
        if user_input is not None:
            extended_logging = user_input.get("extended_logging", extended_logging)
            if user_input.get("reset_defaults"):
                polling_yaml = DEFAULT_POLLING_YAML
            else:
                polling_yaml = user_input["polling_yaml"]
            # YAML validieren
            try:
                yaml.safe_load(polling_yaml)
            except Exception:
                errors["polling_yaml"] = "invalid_yaml"
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={"polling_yaml": polling_yaml, 
                          "extended_logging": extended_logging}
                )

        # Nur anzeigen, wenn Polling aktiviert ist
        if not self._polling_enabled:
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional("polling_yaml", default=polling_yaml): selector({
                    "text": {
                        "multiline": True,
                        "multiple": False
                    }
                }),
                vol.Optional("reset_defaults", default=False): bool,
                vol.Optional("extended_logging", default=extended_logging): bool
            }),
            errors=errors,
        )
    