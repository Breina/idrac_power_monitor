"""Base entity bound to an iDRAC coordinator."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IdracCoordinator


class IdracEntity(CoordinatorEntity[IdracCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: IdracCoordinator, unique_suffix: str, name: str | None):
        super().__init__(coordinator)
        info = coordinator.info
        # Same unique ids as 1.x so upgrades keep entity ids and history
        self._attr_unique_id = f'{info.serial}_{info.model}_{unique_suffix}'
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info.serial)},
            name=info.model,
            manufacturer=info.manufacturer,
            model=info.model,
            sw_version=info.firmware,
            serial_number=info.serial,
            configuration_url=coordinator.client.base_url,
        )
