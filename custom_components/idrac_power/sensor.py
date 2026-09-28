"""Power, energy, fan and temperature sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import REVOLUTIONS_PER_MINUTE, UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IdracConfigEntry
from .coordinator import IdracCoordinator
from .entity import IdracEntity, add_entities_as_they_appear


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
