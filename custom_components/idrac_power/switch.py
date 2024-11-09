"""Platform for iDrac power switch integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.entity import DeviceInfo

from .const import (DOMAIN, DATA_IDRAC_REST_CLIENT, JSON_MODEL, JSON_MANUFACTURER, JSON_SERIAL_NUMBER, DATA_IDRAC_INFO,
                    DATA_IDRAC_FIRMWARE)
from .idrac_rest import IdracRest, CannotConnect, RedfishConfig

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Add iDrac power sensor entry"""
    rest_client = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_REST_CLIENT]

    try:
        if DATA_IDRAC_INFO not in hass.data[DOMAIN][entry.entry_id]:
            info = await hass.async_add_executor_job(target=rest_client.get_device_info)
            if not info:
                raise PlatformNotReady("Could not set up: device didn't return anything.")

            hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_INFO] = info
        else:
            info = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_INFO]

        firmware_version = await hass.async_add_executor_job(target=rest_client.get_firmware_version)
        if not firmware_version:
            if DATA_IDRAC_FIRMWARE in hass.data[DOMAIN][entry.entry_id]:
                firmware_version = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_FIRMWARE]
        else:
            hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_INFO] = firmware_version
    except (CannotConnect, RedfishConfig) as e:
        raise PlatformNotReady(str(e)) from e

    model = info[JSON_MODEL]
    name = model
    manufacturer = info[JSON_MANUFACTURER]
    serial = info[JSON_SERIAL_NUMBER]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=name,
        manufacturer=manufacturer,
        model=model,
        sw_version=firmware_version,
        serial_number=serial
    )

    async_add_entities([
        IdracPowerSwitch(hass, rest_client, device_info, f"{serial}_{name}_power_on", name)
    ])


class IdracPowerSwitch(SwitchEntity):
    def __init__(self, hass: HomeAssistant, rest: IdracRest, device_info: dict, unique_id: str, name: str):
        self.hass = hass
        self.rest = rest

        self.entity_description = SwitchEntityDescription(
            key='power',
            name=f"Power {name}",
            icon='mdi:power',
            device_class=SwitchDeviceClass.SWITCH
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

        self.rest.register_callback_status(self.update_value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.rest.idrac_reset, 'On')

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self.rest.idrac_reset, 'GracefulShutdown')

    def update_value(self, status: bool | None):
        self._attr_is_on = status
        self.schedule_update_ha_state()
