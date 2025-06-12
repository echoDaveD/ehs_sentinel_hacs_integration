import voluptuous as vol
from homeassistant import config_entries
import socket
import asyncio
from .const import DOMAIN

class EHSSentinelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for EHS Sentinel."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            valid = await self._test_connection(user_input["ip"], user_input["port"])
            if not valid:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="EHS Sentinel TCP", data=user_input)

        schema = vol.Schema({
            vol.Required("ip"): str,
            vol.Required("port"): int,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def _test_connection(self, ip, port):
        """Testet, ob eine TCP-Verbindung aufgebaut werden kann."""
        try:
            loop = asyncio.get_running_loop()
            fut = loop.getaddrinfo(ip, port)
            await fut  # DNS-Check
            reader, writer = await asyncio.open_connection(ip, port)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False