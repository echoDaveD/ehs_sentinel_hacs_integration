from homeassistant import config_entries
from homeassistant.helpers.selector import selector
from homeassistant.helpers import entity_registry, device_registry
import voluptuous as vol
import asyncio
import yaml
import os
from .const import DOMAIN, DEFAULT_POLLING_YAML

CONFIG_SCHEMA = vol.Schema({
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
                    vol.Required("indoor_channel", default=0): selector({
                        "number": {
                            "min": 0,
                            "max": 255,
                            "mode": "box",
                            "step": 1,
                            "unit_of_measurement": ""
                        }
                    }),
                    vol.Required("indoor_address", default=0): selector({
                        "number": {
                            "min": 0,
                            "max": 255,
                            "mode": "box",
                            "step": 1,
                            "unit_of_measurement": ""
                        }
                    }),
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

    VERSION = 1
    
    async def async_step_user(self, user_input=None):
        errors = {}
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        
        if user_input is not None:
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
                self.ip = user_input["ip"]
                self.port = user_input["port"]
                self.polling = user_input["polling"]
                self.polling_yaml = user_input["polling_yaml"]
                self.write_mode = user_input["write_mode"]  
                self.extended_logging = user_input["extended_logging"] 
                self.indoor_channel = user_input["indoor_channel"]
                self.indoor_address = user_input["indoor_address"]

                return self.async_create_entry(
                    title=f"{self.ip}",
                    data={
                        "ip": self.ip,
                        "port": self.port,
                        "polling": self.polling,
                        "polling_yaml": self.polling_yaml,
                        "write_mode": self.write_mode,
                        "extended_logging": self.extended_logging,
                        "polling_yaml": self.polling_yaml,
                        "indoor_channel": self.indoor_channel,
                        "indoor_address": self.indoor_address,
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
        self.ip = config_entry.data.get("ip")
        self._polling_enabled = config_entry.options.get("polling", config_entry.data.get("polling", False))
        self._polling_yaml = config_entry.options.get("polling_yaml", config_entry.data.get("polling_yaml", DEFAULT_POLLING_YAML))
        self._write_mode = config_entry.options.get("write_mode", config_entry.data.get("write_mode", False))
        self._extended_logging = config_entry.options.get("extended_logging", config_entry.data.get("extended_logging", False))
        self._indoor_address = config_entry.options.get("indoor_address", config_entry.data.get("indoor_address", 0))
        self._indoor_channel = config_entry.options.get("indoor_channel", config_entry.data.get("indoor_channel", 0))

    async def async_step_init(self, user_input=None):
        errors = {}
        polling_yaml = self._polling_yaml
        extended_logging = self._extended_logging
        write_mode = self._write_mode
        polling_enabled = self._polling_enabled
        indoor_address = self._indoor_address
        indoor_channel = self._indoor_channel
        if user_input is not None:
            extended_logging = user_input.get("extended_logging", extended_logging)
            if user_input.get("reset_defaults"):
                polling_yaml = DEFAULT_POLLING_YAML
                write_mode = False
                polling_enabled = False
                indoor_address = 0
                indoor_channel = 0
            else:
                polling_yaml = user_input["polling_yaml"]
                write_mode = user_input["write_mode"]
                polling_enabled = user_input["polling"]
                indoor_address = user_input["indoor_address"]
                indoor_channel = user_input["indoor_channel"]
            # YAML validieren
            try:
                yaml.safe_load(polling_yaml)
            except Exception:
                errors["polling_yaml"] = "invalid_yaml"
            if not errors:
                return await self._update_and_reload({
                        "polling": polling_enabled,
                        "polling_yaml": polling_yaml,
                        "write_mode": write_mode,
                        "extended_logging": extended_logging,
                        "polling_yaml": polling_yaml,
                        "indoor_channel": indoor_channel,
                        "indoor_address": indoor_address,
                    }, f"{self.ip}")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                    vol.Required("write_mode", default=write_mode): bool,
                    vol.Required("polling", default=polling_enabled): bool,
                    vol.Required("polling_yaml", default=polling_yaml): selector({
                        "text": {
                            "multiline": True,
                            "multiple": False
                        }
                    }),
                    vol.Required("extended_logging", default=extended_logging): bool,
                    vol.Required("indoor_channel", default=indoor_channel): selector({
                        "number": {
                            "min": 0,
                            "max": 255,
                            "mode": "box",
                            "step": 1,
                            "unit_of_measurement": ""
                        }
                    }),
                    vol.Required("indoor_address", default=indoor_address): selector({
                        "number": {
                            "min": 0,
                            "max": 255,
                            "mode": "box",
                            "step": 1,
                            "unit_of_measurement": ""
                        }
                    }),
                }),
            errors=errors,
        )
    
    async def _update_and_reload(self, new_options: dict, title: str):
        self.hass.config_entries.async_update_entry(self.config_entry, options=new_options)
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(title=title, data=new_options)
    