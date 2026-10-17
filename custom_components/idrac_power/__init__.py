"""iDRAC power usage monitor"""
from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .client import CannotConnect, InvalidAuth, RedfishConfig, SessionLimit
from .const import CONF_API, HOST, PASSWORD, USERNAME
from .coordinator import IdracCoordinator
from .factory import async_create_client

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SWITCH, Platform.EVENT]

# Unique id suffixes 1.8.0 made up for list items without identifiers
PHANTOM_SUFFIX = re.compile(r'psu_\d+|fan_None|temp_None')

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
    _remove_phantom_entities(hass, entry, coordinator)

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_phantom_entities(hass: HomeAssistant, entry: IdracConfigEntry, coordinator: IdracCoordinator) -> None:
    """Remove the entities 1.8.0 registered from polls without identifiers.

    Such polls produced PSUs keyed on their list position ("psu_0", named
    "PSU 1") and fans / temperatures keyed on "None"; no later poll provides
    them, so they stay unavailable forever. Entities the iDRAC reports right
    now are kept, whatever their id.
    """
    data = coordinator.data
    provided = ({f'psu_{key}' for key in data.power_supplies} | {f'fan_{key}' for key in data.fans}
                | {f'temp_{key}' for key in data.temperatures})
    prefix = f'{coordinator.info.serial}_{coordinator.info.model}_'
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        suffix = entity.unique_id.removeprefix(prefix)
        if PHANTOM_SUFFIX.fullmatch(suffix) and suffix not in provided:
            _LOGGER.info('Removing %s, registered from an iDRAC answer without identifiers', entity.entity_id)
            registry.async_remove(entity.entity_id)


async def _async_reload(hass: HomeAssistant, entry: IdracConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IdracConfigEntry) -> bool:
    """Unload an iDRAC config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client = entry.runtime_data.client
        await client.close()
        client.session.detach()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: IdracConfigEntry) -> None:
    """Forget which event log entries were already reported."""
    from .coordinator import ALERT_STORE_VERSION
    from .const import DOMAIN
    await Store(hass, ALERT_STORE_VERSION, f'{DOMAIN}.{entry.entry_id}.alerts').async_remove()
