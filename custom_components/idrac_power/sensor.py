"""Power, energy, fan, temperature and per power supply sensors."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import IdracConfigEntry
from .client import IdracData
from .entity import IdracDescribedEntity, IdracItemEntity, add_entities_as_they_appear, described_entities


@dataclass(frozen=True, kw_only=True)
class IdracSensorDescription(SensorEntityDescription):
    """A server-wide reading."""
    value_fn: Callable[[IdracData], StateType]
    exists_fn: Callable[[IdracData], bool] = lambda data: True


@dataclass(frozen=True, kw_only=True)
class IdracItemSensorDescription(SensorEntityDescription):
    """A reading of each item `items_fn` returns: fans, temperature probes or PSUs."""
    items_fn: Callable[[IdracData], Mapping[str, Any]]
    name_fn: Callable[[Any], str]
    value_fn: Callable[[Any], StateType]
    exists_fn: Callable[[Any], bool] = lambda item: True


SENSORS = (
    IdracSensorDescription(
        key='power',
        name='Power usage',
        icon='mdi:lightning-bolt',
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.power_watts,
    ),
    IdracSensorDescription(
        key='energy',
        name='Energy consumption',
        icon='mdi:lightning-bolt-circle',
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        # The counter can be reset from the iDRAC UI: total_increasing handles that
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.energy_kwh,
        exists_fn=lambda data: data.energy_kwh is not None,
    ),
)

ITEM_SENSORS = (
    IdracItemSensorDescription(
        key='fan',
        icon='mdi:fan',
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        items_fn=lambda data: data.fans,
        name_fn=lambda fan: fan.name,
        value_fn=lambda fan: fan.value,
    ),
    IdracItemSensorDescription(
        key='temp',
        icon='mdi:thermometer',
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        items_fn=lambda data: data.temperatures,
        name_fn=lambda temp: temp.name,
        value_fn=lambda temp: temp.value,
    ),
    # Power one PSU draws from its feed: with two feeds, what each one supplies
    IdracItemSensorDescription(
        key='psu_power',
        icon='mdi:power-plug',
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        items_fn=lambda data: data.power_supplies,
        name_fn=lambda psu: f'{psu.label} input power',
        value_fn=lambda psu: psu.input_watts,
        exists_fn=lambda psu: psu.input_watts is not None,
    ),
    # 0 V when the PSU lost its feed: with each PSU on its own feed, this tells
    # which feeds are live, which PSU health alone does not (a PSU with a failed
    # fan still draws power)
    IdracItemSensorDescription(
        key='psu_volts',
        icon='mdi:sine-wave',
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        items_fn=lambda data: data.power_supplies,
        name_fn=lambda psu: f'{psu.label} input voltage',
        value_fn=lambda psu: psu.input_volts,
        exists_fn=lambda psu: psu.input_volts is not None,
    ),
    # What iDRAC 6 measures for each PSU, in place of its power (the iDRAC 6 only
    # reports the whole server's)
    IdracItemSensorDescription(
        key='psu_current',
        icon='mdi:current-ac',
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        items_fn=lambda data: data.power_supplies,
        name_fn=lambda psu: f'{psu.label} input current',
        value_fn=lambda psu: psu.input_amps,
        exists_fn=lambda psu: psu.input_amps is not None,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = entry.runtime_data
    add_entities_as_they_appear(coordinator, async_add_entities, lambda: described_entities(
        coordinator, SENSORS, IdracSensor, ITEM_SENSORS, IdracItemSensor))


class IdracSensor(IdracDescribedEntity, SensorEntity):
    entity_description: IdracSensorDescription

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)


class IdracItemSensor(IdracItemEntity, SensorEntity):
    entity_description: IdracItemSensorDescription

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.item)
