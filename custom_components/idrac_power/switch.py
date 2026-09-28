"""Server power switch."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IdracConfigEntry
from .client import POWER_GRACEFUL_SHUTDOWN, POWER_ON
from .coordinator import IdracCoordinator
from .entity import IdracEntity


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry, async_add_entities: AddEntitiesCallback):
    async_add_entities([IdracPowerSwitch(entry.runtime_data)])


class IdracPowerSwitch(IdracEntity, SwitchEntity):
    _attr_icon = 'mdi:power'
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: IdracCoordinator):
        # Shares its unique id suffix with the power-on button, as in 1.x
        super().__init__(coordinator, 'power_on', 'Power')

    @property
    def is_on(self):
        return self.coordinator.data.power_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_power(POWER_ON)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_power(POWER_GRACEFUL_SHUTDOWN)
        await self.coordinator.async_request_refresh()
