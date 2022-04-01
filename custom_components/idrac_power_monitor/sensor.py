"""Platform for Schneider Energy."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import (DOMAIN, CURRENT_POWER_SENSOR_DESCRIPTION, DATA_IDRAC_REST_CLIENT, JSON_NAME, JSON_MODEL,
                    JSON_MANUFACTURER,
                    JSON_SERIAL_NUMBER, TOTAL_POWER_SENSOR_DESCRIPTION)
from .schneider_modbus import SchneiderModbus

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Add all the sensor entities"""
    modbus_client = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_REST_CLIENT]

    # TODO figure out how to properly do async stuff in Python lol
    info = await hass.async_add_executor_job(target=modbus_client.get_device_info)
    firmware_version = await hass.async_add_executor_job(target=modbus_client.get_firmware_version)

    name = info[JSON_NAME]
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
        IdracCurrentPowerSensor(modbus_client, device_info, f"{serial}_{model}_current", model),
        IdracTotalPowerSensor(modbus_client, device_info, f"{serial}_{model}_total", model)
    ])


class IdracCurrentPowerSensor(SensorEntity):
    """The iDrac's current power sensor entity."""

    def __init__(self, rest: SchneiderModbus, device_info, unique_id, model):
        self.rest = rest

        self.entity_description = CURRENT_POWER_SENSOR_DESCRIPTION
        self.entity_description.name = model + self.entity_description.name
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id

        self._attr_native_value = None

    def update(self) -> None:
        """Get the latest data from the iDrac."""

        self._attr_native_value = self.rest.get_power_usage()


class IdracTotalPowerSensor(SensorEntity):
    """The iDrac's total power sensor entity."""

    def __init__(self, rest: SchneiderModbus, device_info, unique_id, model):
        self.rest = rest

        self.entity_description = TOTAL_POWER_SENSOR_DESCRIPTION
        self.entity_description.name = model + self.entity_description.name
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id

        self.last_update = datetime.now()

        self._attr_native_value = 0.0

    def update(self) -> None:
        """Get the latest data from the iDrac."""

        now = datetime.now()
        seconds_between = (now - self.last_update).total_seconds()
        hours_between = seconds_between / 3600.0

        self._attr_native_value += self.rest.get_power_usage() * hours_between

        self.last_update = now
