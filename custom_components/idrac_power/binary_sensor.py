"""Server power state, overall health and power supply health."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IdracConfigEntry
from .coordinator import IdracCoordinator
from .entity import IdracEntity, add_entities_as_they_appear


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = entry.runtime_data

    def build():
        data = coordinator.data
        yield 'status', lambda: IdracStatusBinarySensor(coordinator)
        if data.health_ok is not None:
            yield 'health', lambda: IdracHealthBinarySensor(coordinator)
        for psu_id, (name, _) in data.power_supplies.items():
            yield f'psu_{psu_id}', lambda i=psu_id, n=name: IdracPsuBinarySensor(coordinator, i, n)

    add_entities_as_they_appear(coordinator, async_add_entities, build)


class IdracStatusBinarySensor(IdracEntity, BinarySensorEntity):
    """Whether the server is powered on (not whether the iDRAC is up)."""
    _attr_icon = 'mdi:power'
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'status', 'Server status')

    @property
    def is_on(self):
        return self.coordinator.data.power_on


class IdracHealthBinarySensor(IdracEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'health', 'Hardware health')

    @property
    def is_on(self):
        healthy = self.coordinator.data.health_ok
        return None if healthy is None else not healthy


class IdracPsuBinarySensor(IdracEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: IdracCoordinator, psu_id: str, name: str):
        super().__init__(coordinator, f'psu_{psu_id}', name)
        self.psu_id = psu_id

    @property
    def available(self) -> bool:
        return super().available and self.psu_id in self.coordinator.data.power_supplies

    @property
    def is_on(self):
        healthy = self.coordinator.data.power_supplies[self.psu_id][1]
        return None if healthy is None else not healthy
