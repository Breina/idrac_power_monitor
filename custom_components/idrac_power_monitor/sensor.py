"""Platform for iDrac power sensor integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from datetime import datetime
from homeassistant.const import CONF_HOST
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.binary_sensor import BinarySensorEntity
import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from requests import RequestException

from .const import (DOMAIN, CURRENT_POWER_SENSOR_DESCRIPTION, DATA_IDRAC_REST_CLIENT, JSON_NAME, JSON_MODEL,
                    JSON_MANUFACTURER,
                    JSON_SERIAL_NUMBER, FAN_SENSOR_DESCRIPTION, TEMP_SENSOR_DESCRIPTION)
from .idrac_rest import IdracRest
import asyncio
import time

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
    thermal_info = await hass.async_add_executor_job(target=rest_client.update_thermals)

    model = info[JSON_MODEL]
    name = model
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
    ])
    

    for i,fan in enumerate(thermal_info['Fans']):
        _LOGGER.info("Adding fan %s : %s", i, fan["FanName"])
        async_add_entities([IdracFanSensor(hass, rest_client, device_info, f"{model}_fan_{i}",  fan["FanName"], i) ])
    

    for i,temp in enumerate(thermal_info['Temperatures']):
        _LOGGER.info("Adding temp %s : %s", i, temp["Name"])
        async_add_entities([
            IdracTempSensor(hass, rest_client, device_info, f"{model}_temp_{i}",  temp["Name"], i)])

    async def refresh_sensors_task(hass):
        while True:
            _LOGGER.warn("Refreshing sensors")
            await hass.async_add_executor_job(rest_client.update_thermals)
            await hass.async_add_executor_job(rest_client.update_status)
            await hass.async_add_executor_job(rest_client.update_power_usage)
            await asyncio.sleep(rest_client.interval)
            
    def start_sensors_task(event):
        _LOGGER.info("Starting sensors task")
        task = hass.async_create_task(refresh_sensors_task(hass))
            
    hass.bus.async_listen_once('homeassistant_started', start_sensors_task)
            
class IdracCurrentPowerSensor(SensorEntity):
    """The iDrac's current power sensor entity."""

    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = CURRENT_POWER_SENSOR_DESCRIPTION
        self.entity_description.name = name

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

        self._attr_native_value = None

    async def async_update(self) -> None:
        """Get the latest data from the iDrac."""
        self._attr_native_value = self.rest.get_power_usage()
    
    @property
    def name(self):
        """Name of the entity."""
        return "Power Usage"
    
class IdracFanSensor(SensorEntity):
    id = 0
    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name, id):
        self.hass = hass
        self.rest = rest

        self.entity_description = FAN_SENSOR_DESCRIPTION
        self.entity_description.name = name
        self.custom_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        
        self._attr_has_entity_name = True
        
        self._attr_native_value = None
        self.id = id
    @property
    def name(self):
        """Name of the entity."""
        return self.custom_name
    
    async def async_update(self) -> None:
        """Get the latest data from the iDrac."""
        thermal = self.rest.get_thermals()
        self._attr_native_value = thermal['Fans'][self.id]['Reading']

class IdracTempSensor(SensorEntity):
    id = 0
    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name, id):
        self.hass = hass
        self.rest = rest

        self.entity_description = TEMP_SENSOR_DESCRIPTION
        self.entity_description.name = name
        self.custom_name = name
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id

        self._attr_native_value = None
        self.id = id
        
    @property
    def name(self):
        """Name of the entity."""
        return self.custom_name
    
    async def async_update(self) -> None:
        """Get the latest data from the iDrac."""
        thermal = self.rest.get_thermals()
        self._attr_native_value = thermal['Temperatures'][self.id]['ReadingCelsius']
