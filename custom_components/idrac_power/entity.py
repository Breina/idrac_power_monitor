"""Base entity bound to an iDRAC coordinator."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IdracCoordinator


def add_entities_as_they_appear(coordinator: IdracCoordinator, async_add_entities: AddEntitiesCallback,
                                build: Callable[[], Iterable[tuple[str, Callable[[], Entity]]]]) -> None:
    """Add entities now, and later for sensors that show up afterwards.

    An iDRAC publishes no fan, temperature nor PSU readings while the host is
    powered off: those entities must appear when the server is turned on.
    `build` yields (key, constructor) pairs for what the current data holds.
    """
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = []
        for key, make in build():
            if key not in known:
                known.add(key)
                new.append(make())
        if new:
            async_add_entities(new)

    add_new()
    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(add_new))


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
