"""Base entities bound to an iDRAC coordinator, and how they are added."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from functools import partial
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IdracCoordinator

Builder = Callable[[], Iterable[tuple[str, Callable[[], Entity]]]]


def add_entities_as_they_appear(coordinator: IdracCoordinator, async_add_entities: AddEntitiesCallback,
                                build: Builder) -> None:
    """Add entities now, and later for sensors that show up afterwards.

    An iDRAC publishes no fan, temperature nor PSU readings while the host is
    powered off: those entities must appear when the server is turned on.
    `build` yields (unique id suffix, constructor) pairs for what the current
    data holds.
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


def described_entities(coordinator: IdracCoordinator, descriptions: Iterable[EntityDescription],
                       entity_class: type[IdracEntity], item_descriptions: Iterable[EntityDescription],
                       item_entity_class: type[IdracItemEntity]) -> Iterator[tuple[str, Callable[[], Entity]]]:
    """What `add_entities_as_they_appear` adds for a platform's entity descriptions.

    `descriptions` are server-wide: one entity each, once `exists_fn(data)`.
    `item_descriptions` give one entity per item of `items_fn(data)` (each
    fan, temperature probe or PSU), once `exists_fn(item)`.
    """
    data = coordinator.data
    for description in descriptions:
        if description.exists_fn(data):
            yield description.key, partial(entity_class, coordinator, description)
    for description in item_descriptions:
        for item_id, item in description.items_fn(data).items():
            if description.exists_fn(item):
                yield (f'{description.key}_{item_id}',
                       partial(item_entity_class, coordinator, description, item_id, item))


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


class IdracDescribedEntity(IdracEntity):
    """A server-wide entity, named and keyed by its description."""

    def __init__(self, coordinator: IdracCoordinator, description: EntityDescription):
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description


class IdracItemEntity(IdracEntity):
    """The entity of one fan, temperature probe or PSU; unavailable while the iDRAC does not list it."""

    def __init__(self, coordinator: IdracCoordinator, description: EntityDescription, item_id: str, item: Any):
        # "<key>_<item id>", e.g. fan_0x17||Fan.Embedded.1A, as in 1.x
        super().__init__(coordinator, f'{description.key}_{item_id}', description.name_fn(item))
        self.entity_description = description
        self.item_id = item_id

    @property
    def item(self) -> Any:
        return self.entity_description.items_fn(self.coordinator.data).get(self.item_id)

    @property
    def available(self) -> bool:
        return super().available and self.item is not None
