"""iDRAC power usage monitor"""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, DATA_IDRAC_REST_CLIENT, HOST, USERNAME, PASSWORD, CONF_INTERVAL, CONF_INTERVAL_DEFAULT
from .client_factory import create_client

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the iDRAC connection from a config entry."""

    rest_client = await hass.async_add_executor_job(
        create_client,
        entry.data[HOST],
        entry.data[USERNAME],
        entry.data[PASSWORD],
        entry.data.get(CONF_INTERVAL, CONF_INTERVAL_DEFAULT),
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_IDRAC_REST_CLIENT: rest_client
    }

    platforms = [Platform.SENSOR]
    if getattr(rest_client, "supports_status", True):
        platforms.append(Platform.BINARY_SENSOR)
    if getattr(rest_client, "supports_power_control", True):
        platforms.extend([Platform.BUTTON, Platform.SWITCH])

    await hass.config_entries.async_forward_entry_setups(
        entry, platforms
    )

    async def refresh_sensors_task():
        while True:
            _LOGGER.debug("Refreshing sensors")
            await update_all()
            await asyncio.sleep(rest_client.interval)

    async def update_all():
        try:
            await hass.async_add_executor_job(rest_client.update_thermals)
        except Exception as e:
            # ignore exceptions, just log the error
            _LOGGER.warning(f"Updating {entry.entry_id} thermals sensors failed:\n{e}")

        try:
            await hass.async_add_executor_job(rest_client.update_status)
        except Exception as e:
            # ignore exceptions, just log the error
            _LOGGER.warning(f"Updating {entry.entry_id} status sensor failed:\n{e}")

        try:
            await hass.async_add_executor_job(rest_client.update_power_usage)
        except Exception as e:
            # ignore exceptions, just log the error
            _LOGGER.warning(f"Updating {entry.entry_id} power usage failed:\n{e}")

    hass.async_create_background_task(refresh_sensors_task(), f"Update {entry.entry_id} iDRAC task")

    return True
