from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN

class EHSSentinelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="EHS Sentinel", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("serial_port"): str,
                vol.Optional("mqtt_host", default="localhost"): str,
                vol.Optional("dry_run", default=False): bool
            })
        )
