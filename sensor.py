"""Platform for iDrac power sensor integration."""
from __future__ import annotations

import backoff as backoff
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from requests import RequestException

from idrac_rest import IdracRest
from .const import (JSON_SERIAL_NUMBER, JSON_MODEL, DOMAIN,
                    SENSOR_DESCRIPTION, DATA_IDRAC_REST_CLIENT)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Add iDrac power sensor entry"""
    rest_client = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_REST_CLIENT]

    name, model, manufacturer, serial = rest_client.get_device_info()
    firmware_version = rest_client.get_firmware_version()

    device_info = {
        'identifiers': {(DOMAIN, model, serial)},
        'name': name,
        'manufacturer': manufacturer,
        'model': model,
        'sw_version': firmware_version
    }

    async_add_entities(IdracPowerSensor(rest_client, device_info))


class IdracPowerSensor(SensorEntity):
    """The iDrac's power sensor entity."""

    def __init__(self, rest: IdracRest, device_info):
        self._state = None
        self.rest = rest

        self.entity_description = SENSOR_DESCRIPTION
        self._attr_device_info = device_info
        self._attr_unique_id = f"{device_info[JSON_SERIAL_NUMBER]}_{device_info[JSON_MODEL]}"

        self._state = None

    def update(self) -> None:
        """Get the latest data from the iDrac."""

        @backoff.on_exception(backoff.expo, exception=RequestException, max_time=120)
        def _get_value():
            return self.rest.get_power_usage

        self._state = _get_value()
