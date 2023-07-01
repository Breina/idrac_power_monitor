"""Platform for iDrac power sensor integration."""
from __future__ import annotations

import logging
from datetime import datetime
from homeassistant.const import CONF_HOST
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.binary_sensor import BinarySensorEntity

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from requests import RequestException

from .const import (DOMAIN, CURRENT_POWER_SENSOR_DESCRIPTION, DATA_IDRAC_REST_CLIENT, JSON_NAME, JSON_MODEL,
                    JSON_MANUFACTURER,
                    JSON_SERIAL_NUMBER, STATUS_BINARY_SENSOR_DESCRIPTION)
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
        IdracStatusBinarySensor(hass, rest_client, device_info, f"{serial}_{model}_status", f"{model}_status"),
    ])
    

class IdracStatusBinarySensor(BinarySensorEntity):
    """The iDrac's current power sensor entity."""

    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = STATUS_BINARY_SENSOR_DESCRIPTION
        self.entity_description.name = name

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True


    async def async_update(self) -> None:
        """Get the latest data from the iDrac."""

        self._attr_is_on = await self.hass.async_add_executor_job(self.rest.get_status)

    @property
    def name(self):
        """Name of the entity."""
        return "Server Status"