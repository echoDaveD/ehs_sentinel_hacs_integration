import asyncio
import logging
from .const import DOMAIN
from .sensor import DynamicSensor, DynamicBinarySensor, DynamicNumber

_LOGGER = logging.getLogger(__name__)

async def start_ehs_sentinel(hass, config):
    _LOGGER.info("EHS Sentinel is starting...")

    await asyncio.sleep(5)  # simulate delay

    cb = hass.data[DOMAIN]["sensor_add_cb"]

    # Dynamisch Sensor hinzufügen
    sensor = DynamicSensor("PV Leistung", 215.2)
    binary = DynamicBinarySensor("Türkontakt", True)
    number = DynamicNumber("Sollwert", 22.0)

    # Registriere die Entitäten
    cb([sensor, binary, number])

    # Optional: speichere Referenzen für spätere Updates
    hass.data[DOMAIN]["entities"].extend([sensor, binary, number])

    # Simuliere Zustand-Update alle 15 Sekunden
    async def update_loop():
        while True:
            await asyncio.sleep(15)
            number._attr_native_value += 0.5
            number.async_write_ha_state()

    hass.async_create_task(update_loop())
    _LOGGER.info("EHS Sentinel started successfully.")