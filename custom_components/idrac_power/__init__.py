"""iDRAC power usage monitor"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .client import CannotConnect, InvalidAuth, RedfishConfig, SessionLimit
from .const import CONF_API, HOST, PASSWORD, USERNAME
from .coordinator import IdracCoordinator
from .factory import async_create_client

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SWITCH]

type IdracConfigEntry = ConfigEntry[IdracCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry) -> bool:
    """Set up the iDRAC connection from a config entry."""
    try:
        client, info = await async_create_client(
            hass, entry.data[HOST], entry.data[USERNAME], entry.data[PASSWORD], entry.data.get(CONF_API)
        )
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed(f'Credentials rejected by {entry.data[HOST]}') from err
    except (CannotConnect, RedfishConfig, SessionLimit) as err:
        raise ConfigEntryNotReady(str(err)) from err

    if CONF_API not in entry.data:
        # Entries from 1.x: remember what was detected, no probing next time
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_API: client.api})

    coordinator = IdracCoordinator(hass, entry, client, info)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise
    entry.runtime_data = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: IdracConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IdracConfigEntry) -> bool:
    """Unload an iDRAC config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client = entry.runtime_data.client
        await client.close()
        await client.session.close()
    return unload_ok
