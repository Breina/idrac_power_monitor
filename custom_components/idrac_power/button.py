"""Platform for iDrac power sensor integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription, ButtonDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import (DOMAIN, DATA_IDRAC_REST_CLIENT, JSON_MODEL, JSON_MANUFACTURER, JSON_SERIAL_NUMBER)
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
        identifiers={('serial', serial)},
        name=name,
        manufacturer=manufacturer,
        model=model,
        sw_version=firmware_version,
        serial_number=serial
    )

    async_add_entities([
        IdracPowerONButton(hass, rest_client, device_info, f"{serial}_{name}_power_on", name),
        IdracRefreshButton(hass, rest_client, device_info, f"{serial}_{name}_refresh", name)
    ])


class IdracPowerONButton(ButtonEntity):

    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = ButtonEntityDescription(
            key='power_on',
            name=f"Power on {name}",
            icon='mdi:power',
            device_class=ButtonDeviceClass.UPDATE,
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

    async def async_press(self) -> None:
        await self.hass.async_add_executor_job(self.rest.power_on)


class IdracRefreshButton(ButtonEntity):

    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = ButtonEntityDescription(
            key='refresh',
            name=f"Refresh {name}",
            icon='mdi:power',
            device_class=ButtonDeviceClass.UPDATE,
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

    async def async_press(self) -> None:
        _LOGGER.info("Refreshing sensors manually")
        await self.hass.async_add_executor_job(self.rest.update_thermals)
        await self.hass.async_add_executor_job(self.rest.update_status)
        await self.hass.async_add_executor_job(self.rest.update_power_usage)
