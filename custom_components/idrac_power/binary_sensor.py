"""Server power state, overall health and power supply health."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IdracConfigEntry
from .client import IdracData, active_problems
from .entity import IdracDescribedEntity, IdracItemEntity, add_entities_as_they_appear, described_entities


def problem(healthy: bool | None) -> bool | None:
    """A problem sensor is on when unhealthy, unknown when health is."""
    return None if healthy is None else not healthy


def health_attributes(data: IdracData) -> dict[str, Any] | None:
    """Log entries for problems not cleared since, which usually explain a problem state."""
    if data.alerts is None:
        return None
    problems = active_problems(data.alerts)
    attributes: dict[str, Any] = {'active_problems': [f'{alert.severity}: {alert.message}' for alert in problems]}
    if problems:
        latest = problems[-1]
        attributes.update(latest_problem=latest.message, latest_problem_severity=latest.severity,
                          latest_problem_logged_at=latest.created)
    return attributes


@dataclass(frozen=True, kw_only=True)
class IdracBinarySensorDescription(BinarySensorEntityDescription):
    """A server-wide state."""
    value_fn: Callable[[IdracData], bool | None]
    exists_fn: Callable[[IdracData], bool] = lambda data: True
    attributes_fn: Callable[[IdracData], dict[str, Any] | None] = lambda data: None


@dataclass(frozen=True, kw_only=True)
class IdracItemBinarySensorDescription(BinarySensorEntityDescription):
    """A state of each item `items_fn` returns."""
    items_fn: Callable[[IdracData], Mapping[str, Any]]
    name_fn: Callable[[Any], str]
    value_fn: Callable[[Any], bool | None]
    exists_fn: Callable[[Any], bool] = lambda item: True


BINARY_SENSORS = (
    # Whether the server is powered on (not whether the iDRAC is up)
    IdracBinarySensorDescription(
        key='status',
        name='Server status',
        icon='mdi:power',
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda data: data.power_on,
    ),
    IdracBinarySensorDescription(
        key='health',
        name='Hardware health',
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: problem(data.health_ok),
        exists_fn=lambda data: data.health_ok is not None,
        attributes_fn=health_attributes,
    ),
    # On when the server would not survive losing a power supply
    IdracBinarySensorDescription(
        key='psu_redundancy',
        name='PSU redundancy',
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: problem(data.psu_redundancy_ok),
        exists_fn=lambda data: data.psu_redundancy_ok is not None,
    ),
)

ITEM_BINARY_SENSORS = (
    IdracItemBinarySensorDescription(
        key='psu',
        device_class=BinarySensorDeviceClass.PROBLEM,
        items_fn=lambda data: data.power_supplies,
        name_fn=lambda psu: psu.name,
        value_fn=lambda psu: problem(psu.healthy),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = entry.runtime_data
    add_entities_as_they_appear(coordinator, async_add_entities, lambda: described_entities(
        coordinator, BINARY_SENSORS, IdracBinarySensor, ITEM_BINARY_SENSORS, IdracItemBinarySensor))


class IdracBinarySensor(IdracDescribedEntity, BinarySensorEntity):
    entity_description: IdracBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attributes_fn(self.coordinator.data)


class IdracItemBinarySensor(IdracItemEntity, BinarySensorEntity):
    entity_description: IdracItemBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.item)
