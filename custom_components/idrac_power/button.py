"""Platform for iDrac power sensor integration."""
from __future__ import annotations

import logging
from datetime import datetime
from homeassistant.const import CONF_HOST
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.button import ButtonEntity

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from requests import RequestException

from .const import (DOMAIN, DATA_IDRAC_REST_CLIENT, JSON_NAME, JSON_MODEL,
                    JSON_MANUFACTURER,
                    JSON_SERIAL_NUMBER, POWER_ON_DESCRIPTION, REFRESH_DESCRIPTION)
from .idrac_rest import IdracRest
_LOGGER = logging.getLogger(__name__)

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
        IdracPowerONButton(hass, rest_client, device_info, f"{serial}_{model}_status", name),
        IdraRefreshButton(hass, rest_client, device_info, f"{serial}_{model}_refresh", name)
    ])

class IdracPowerONButton(ButtonEntity):
    
    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = POWER_ON_DESCRIPTION
        self.entity_description.name = name

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

    async def async_press(self) -> None:
        result = await self.hass.async_add_executor_job(self.rest.power_on)
        
    @property
    def name(self):
        """Name of the entity."""
        return "Power On" 
            

class IdraRefreshButton(ButtonEntity):
    
    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = POWER_ON_DESCRIPTION
        self.entity_description.name = name

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

    async def async_press(self) -> None:
        _LOGGER.warn("Refreshing sensors")
        await self.hass.async_add_executor_job(self.rest.update_thermals)
        await self.hass.async_add_executor_job(self.rest.update_status)
        await self.hass.async_add_executor_job(self.rest.update_power_usage)
        
    @property
    def name(self):
        """Name of the entity."""
        return "Refresh Values"
            