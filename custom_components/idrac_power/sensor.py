"""Power, energy, fan and temperature sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IdracConfigEntry
from .coordinator import IdracCoordinator
from .entity import IdracEntity, add_entities_as_they_appear
from .events import RECENT, as_dict, problems


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = entry.runtime_data

    def build():
        data = coordinator.data
        yield 'power', lambda: IdracPowerSensor(coordinator)
        if data.energy_kwh is not None:
            yield 'energy', lambda: IdracEnergySensor(coordinator)
        for fan_id, fan in data.fans.items():
            yield f'fan_{fan_id}', lambda i=fan_id, n=fan.name: IdracFanSensor(coordinator, i, n)
        for temp_id, temp in data.temperatures.items():
            yield f'temp_{temp_id}', lambda i=temp_id, n=temp.name: IdracTempSensor(coordinator, i, n)
        for psu_id in data.psu_power:
            psu = data.power_supplies[psu_id]
            yield f'psu_{psu_id}_power', lambda i=psu_id, n=psu.label: IdracPsuPowerSensor(coordinator, i, n)
            if data.energy_kwh is not None:
                yield f'psu_{psu_id}_energy', lambda i=psu_id, n=psu.label: IdracPsuEnergySensor(coordinator, i, n)
        for psu_id, psu in data.power_supplies.items():
            if psu.has_current:
                yield f'psu_{psu_id}_current', lambda i=psu_id, n=psu.label: IdracPsuCurrentSensor(coordinator, i, n)
        if data.events is not None:
            yield 'last_event', lambda: IdracLastEventSensor(coordinator)

    add_entities_as_they_appear(coordinator, async_add_entities, build)


class IdracPowerSensor(IdracEntity, SensorEntity):
    _attr_icon = 'mdi:lightning-bolt'
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'power', 'Power usage')

    @property
    def native_value(self):
        return self.coordinator.data.power_watts


class IdracEnergySensor(IdracEntity, SensorEntity):
    _attr_icon = 'mdi:lightning-bolt-circle'
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    # The counter can be reset from the iDRAC UI: total_increasing handles that
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'energy', 'Energy consumption')

    @property
    def native_value(self):
        return self.coordinator.data.energy_kwh


class IdracPsuSensor(IdracEntity, SensorEntity):
    """A per power supply reading, unavailable while the iDRAC does not list the PSU."""

    def __init__(self, coordinator: IdracCoordinator, psu_id: str, label: str, kind: str):
        super().__init__(coordinator, f'psu_{psu_id}_{kind}', translation_key=f'psu_{kind}',
                         placeholders={'psu': label})
        self.psu_id = psu_id


class IdracPsuPowerSensor(IdracPsuSensor):
    """This PSU's share of the server's measured power (see psu.py)."""
    _attr_icon = 'mdi:lightning-bolt'
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: IdracCoordinator, psu_id: str, label: str):
        super().__init__(coordinator, psu_id, label, 'power')

    @property
    def available(self) -> bool:
        return super().available and self.psu_id in self.coordinator.data.psu_power

    @property
    def native_value(self):
        return self.coordinator.data.psu_power[self.psu_id]


class IdracPsuEnergySensor(IdracPsuSensor):
    """This PSU's share of the server's energy counter, kept across restarts."""
    _attr_icon = 'mdi:lightning-bolt-circle'
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: IdracCoordinator, psu_id: str, label: str):
        super().__init__(coordinator, psu_id, label, 'energy')

    @property
    def available(self) -> bool:
        return super().available and self.psu_id in self.coordinator.data.psu_energy

    @property
    def native_value(self):
        return round(self.coordinator.data.psu_energy[self.psu_id], 4)


class IdracPsuCurrentSensor(IdracPsuSensor):
    """Input current the iDRAC measures on this PSU (0.1 A steps on iDRAC 6)."""
    _attr_icon = 'mdi:current-ac'
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: IdracCoordinator, psu_id: str, label: str):
        super().__init__(coordinator, psu_id, label, 'current')

    @property
    def available(self) -> bool:
        return super().available and self.psu_id in self.coordinator.data.power_supplies

    @property
    def native_value(self):
        return self.coordinator.data.power_supplies[self.psu_id].current


class IdracFanSensor(IdracEntity, SensorEntity):
    _attr_icon = 'mdi:fan'
    _attr_native_unit_of_measurement = REVOLUTIONS_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: IdracCoordinator, fan_id: str, name: str):
        super().__init__(coordinator, f'fan_{fan_id}', name)
        self.fan_id = fan_id

    @property
    def available(self) -> bool:
        return super().available and self.fan_id in self.coordinator.data.fans

    @property
    def native_value(self):
        return self.coordinator.data.fans[self.fan_id].value


class IdracTempSensor(IdracEntity, SensorEntity):
    _attr_icon = 'mdi:thermometer'
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: IdracCoordinator, temp_id: str, name: str):
        super().__init__(coordinator, f'temp_{temp_id}', name)
        self.temp_id = temp_id

    @property
    def available(self) -> bool:
        return super().available and self.temp_id in self.coordinator.data.temperatures

    @property
    def native_value(self):
        return self.coordinator.data.temperatures[self.temp_id].value


class IdracLastEventSensor(IdracEntity, SensorEntity):
    """Newest System Event Log record; the latest ones as attributes."""
    _attr_icon = 'mdi:text-box-search-outline'

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'last_event', 'Last event')

    @property
    def native_value(self):
        events = self.coordinator.data.events
        return events[-1].message[:255] if events else None

    @property
    def extra_state_attributes(self):
        events = self.coordinator.data.events or []
        last = events[-1] if events else None
        return {
            'time': last.created.isoformat() if last and last.created else None,
            'severity': last.severity if last else None,
            'entries': len(events),
            'problems': len(problems(events)),
            'recent': [as_dict(entry) for entry in reversed(events[-RECENT:])],
        }
