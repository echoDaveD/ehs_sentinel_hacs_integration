import voluptuous as vol
from homeassistant import config_entries
import socket
import asyncio
from .const import DOMAIN
import logging
_LOGGER = logging.getLogger(__name__)

class EHSSentinelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for EHS Sentinel."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        _LOGGER.info("Starting EHS Sentinel configuration flow")
        if user_input is not None:
            valid = await self._test_connection(user_input["ip"], user_input["port"])
            if not valid:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="EHS Sentinel TCP", data=user_input)

        schema = vol.Schema({
            vol.Required(msg="IP", default="168.192.2.69", description="IP des RS485 to ETH/LAN Adapters."): str,
            vol.Required(msg="port", default=4196, description="Port des RS485 to ETH/LAN Adapters."): int,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def _test_connection(self, ip, port):
        """Testet, ob eine TCP-Verbindung aufgebaut werden kann."""
        _LOGGER.indfo(f"Testing connection to {ip}:{port}")
        try:
            loop = asyncio.get_running_loop()
            fut = loop.getaddrinfo(ip, port)
            await fut  # DNS-Check
            reader, writer = await asyncio.open_connection(ip, port)
            writer.close()
            _LOGGER.info(f"Connection to {ip}:{port} successful")
            await writer.wait_closed()
            return True
        except Exception:
            return False