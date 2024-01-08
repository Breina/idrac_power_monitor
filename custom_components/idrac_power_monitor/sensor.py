"""Platform for iDrac power sensor integration."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, UnitOfPower, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import (DOMAIN, DATA_IDRAC_REST_CLIENT, JSON_NAME, JSON_MODEL,
                    JSON_MANUFACTURER,
                    JSON_SERIAL_NUMBER)
from .idrac_rest import IdracRest

_LOGGER = logging.getLogger(__name__)

protocol = 'https://'
drac_managers = '/redfish/v1/Managers/iDRAC.Embedded.1'
drac_chassis_path = '/redfish/v1/Chassis/System.Embedded.1'
drac_powercontrol_path = '/redfish/v1/Chassis/System.Embedded.1/Power/PowerControl'


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Add iDrac power sensor entry"""
    rest_client = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_REST_CLIENT]

    # TODO figure out how to properly do async stuff in Python lol
    info = await hass.async_add_executor_job(target=rest_client.get_device_info)
    firmware_version = await hass.async_add_executor_job(target=rest_client.get_firmware_version)

    name = f'{info[JSON_NAME]}({entry.data[CONF_HOST]})'
    model = info[JSON_MODEL]
    manufacturer = info[JSON_MANUFACTURER]
    serial = info[JSON_SERIAL_NUMBER]

    device_info = DeviceInfo(
        identifiers={('domain', DOMAIN), ('model', model), ('serial', serial)},
        name=name,
        manufacturer=manufacturer,
        model=model,
        sw_version=firmware_version
    )

    async_add_entities([
        IdracCurrentPowerSensor(hass, rest_client, device_info, f"{serial}_{model}_current", name),
        IdracTotalPowerSensor(hass, rest_client, device_info, f"{serial}_{model}_total", name)
    ])


class IdracCurrentPowerSensor(SensorEntity):
    """The iDrac's current power sensor entity."""

    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = SensorEntityDescription(
            key="current_power_usage",
            name=name,
            icon="mdi:server",
            native_unit_of_measurement=UnitOfPower.WATT,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id

        self._attr_native_value = None

    async def async_update(self) -> None:
        """Get the latest data from the iDrac."""

        self._attr_native_value = await self.hass.async_add_executor_job(self.rest.get_power_usage)


class IdracTotalPowerSensor(SensorEntity):
    """The iDrac's total power sensor entity."""

    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = SensorEntityDescription(
            key="total_power_usage",
            name=name,
            icon="mdi:server",
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id

        self.last_update = datetime.utcnow()

        self._attr_native_value = 0.0

    async def async_update(self) -> None:
        """Get the latest data from the iDrac."""

        now = datetime.utcnow()
        seconds_between = (now - self.last_update).total_seconds()
        hours_between = seconds_between / 3600.0

        # Convert power usage from W to kW
        power_usage = await self.hass.async_add_executor_job(self.rest.get_power_usage)
        power_usage_kw = power_usage / 1000.0

        self._attr_native_value += power_usage_kw * hours_between

        self.last_update = now
