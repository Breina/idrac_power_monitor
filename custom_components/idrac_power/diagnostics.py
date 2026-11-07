"""Diagnostics download: what the integration reads from the iDRAC, for issue reports."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import IdracConfigEntry

# The service tag identifies the machine (and its owner, to Dell)
TO_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME, 'serial'}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: IdracConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        'entry': async_redact_data({'data': dict(entry.data), 'options': dict(entry.options)}, TO_REDACT),
        'server': async_redact_data(asdict(coordinator.info), TO_REDACT),
        'api': coordinator.client.api,
        'client': coordinator.client.diagnostics(),
        'last_update_success': coordinator.last_update_success,
        'data': asdict(coordinator.data) if coordinator.data is not None else None,
    }
