"""Power on / off, forced off and restart, and manual refresh buttons."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IdracConfigEntry
from .client import (
    POWER_FORCE_OFF,
    POWER_FORCE_RESTART,
    POWER_GRACEFUL_SHUTDOWN,
    POWER_ON,
)
from .coordinator import IdracCoordinator
from .entity import IdracEntity, add_entities_as_they_appear

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = entry.runtime_data
    async_add_entities([
        IdracPowerButton(coordinator, 'power_on', 'Power on', POWER_ON),
        IdracPowerButton(coordinator, 'power_off', 'Power off', POWER_GRACEFUL_SHUTDOWN),
        IdracPowerButton(coordinator, 'force_off', 'Force power off', POWER_FORCE_OFF, 'mdi:power-plug-off'),
        IdracPowerButton(coordinator, 'force_restart', 'Force restart', POWER_FORCE_RESTART, 'mdi:restart-alert',
                         ButtonDeviceClass.RESTART),
        IdracRefreshButton(coordinator),
    ])

    def build():
        if coordinator.data.events is not None:
            yield 'clear_event_log', lambda: IdracClearEventLogButton(coordinator)

    add_entities_as_they_appear(coordinator, async_add_entities, build)


class IdracPowerButton(IdracEntity, ButtonEntity):
    _attr_icon = 'mdi:power'

    def __init__(self, coordinator: IdracCoordinator, unique_suffix: str, name: str, action: str,
                 icon: str = 'mdi:power', device_class: ButtonDeviceClass | None = None):
        super().__init__(coordinator, unique_suffix, name)
        self.action = action
        self._attr_icon = icon
        self._attr_device_class = device_class

    async def async_press(self) -> None:
        await self.coordinator.client.set_power(self.action)
        await self.coordinator.async_request_refresh()


class IdracRefreshButton(IdracEntity, ButtonEntity):
    _attr_icon = 'mdi:refresh'

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'refresh', 'Refresh')

    async def async_press(self) -> None:
        _LOGGER.info('Refreshing %s sensors manually', self.coordinator.client.host)
        await self.coordinator.async_refresh()


class IdracClearEventLogButton(IdracEntity, ButtonEntity):
    """Empty the System Event Log, once what it reported has been dealt with."""
    _attr_icon = 'mdi:notification-clear-all'
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'clear_event_log', 'Clear event log')

    async def async_press(self) -> None:
        await self.coordinator.async_clear_events()
