"""Platform for iDrac power sensor integration."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import (DOMAIN, DATA_IDRAC_REST_CLIENT, JSON_MODEL, JSON_MANUFACTURER, JSON_SERIAL_NUMBER, DATA_IDRAC_INFO,
                    DATA_IDRAC_FIRMWARE, DATA_IDRAC_THERMAL)
from .idrac_rest import IdracRest

_LOGGER = logging.getLogger(__name__)

protocol = 'https://'
drac_managers = '/redfish/v1/Managers/iDRAC.Embedded.1'
drac_chassis_path = '/redfish/v1/Chassis/System.Embedded.1'
drac_powercontrol_path = '/redfish/v1/Chassis/System.Embedded.1/Power/PowerControl'


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Add iDrac power sensor entry"""
    rest_client = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_REST_CLIENT]

    _LOGGER.debug(f"Getting the REST client for {entry.entry_id}")

    if DATA_IDRAC_INFO not in hass.data[DOMAIN][entry.entry_id]:
        info = await hass.async_add_executor_job(target=rest_client.get_device_info)
        if not info:
            _LOGGER.error(f"Could not set up: couldn't reach device.")
            return

        hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_INFO] = info
    else:
        info = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_INFO]

    firmware_version = await hass.async_add_executor_job(target=rest_client.get_firmware_version)
    if not firmware_version:
        if DATA_IDRAC_FIRMWARE in hass.data[DOMAIN][entry.entry_id]:
            firmware_version = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_FIRMWARE]
    else:
        hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_INFO] = firmware_version

    thermal_info = await hass.async_add_executor_job(target=rest_client.update_thermals)
    if not thermal_info:
        if DATA_IDRAC_THERMAL in hass.data[DOMAIN][entry.entry_id]:
            thermal_info = hass.data[DOMAIN][entry.entry_id][DATA_IDRAC_THERMAL]
        else:
            _LOGGER.error(f"Could not set up: couldn't get thermal info.")
            return

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

    _LOGGER.debug(f"Adding new devices to device info {('serial', serial)}")

    entities = [IdracCurrentPowerSensor(hass, rest_client, device_info, f"{serial}_{name}_current", name)]

    for i, fan in enumerate(thermal_info['Fans']):
        _LOGGER.info("Adding fan %s : %s", i, fan["FanName"])
        entities.append(IdracFanSensor(hass, rest_client, device_info, f"{serial}_{name}_fan_{i}",
                                       f"{name} {fan['FanName']}", i
                                       ))

    for i, temp in enumerate(thermal_info['Temperatures']):
        _LOGGER.info("Adding temp %s : %s", i, temp["Name"])
        entities.append(IdracTempSensor(hass, rest_client, device_info, f"{serial}_{name}_temp_{i}",
                                        f"{name} {temp['Name']}", i
                                        ))

    async_add_entities(entities)

    async def refresh_sensors_task():
        while True:
            _LOGGER.debug("Refreshing sensors")
            await update_all()
            await asyncio.sleep(rest_client.interval)

    async def update_all():
        await hass.async_add_executor_job(rest_client.update_thermals)
        await hass.async_add_executor_job(rest_client.update_status)
        await hass.async_add_executor_job(rest_client.update_power_usage)

    await update_all()

    hass.async_create_background_task(refresh_sensors_task(), f"Update {name} iDRAC task")


class IdracCurrentPowerSensor(SensorEntity):
    """The iDrac's current power sensor entity."""

    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name):
        self.hass = hass
        self.rest = rest

        self.entity_description = SensorEntityDescription(
            key='current_power_usage',
            name=f"{name} power usage",
            icon='mdi:lightning-bolt',
            native_unit_of_measurement='W',
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

        self._attr_native_value = None

        self.rest.register_callback_power_usage(self.update_value)

    def update_value(self, new_value: int | None):
        if new_value:
            self._attr_native_value = new_value
            self._attr_available = True
        else:
            self._attr_available = False
        self.schedule_update_ha_state()


class IdracFanSensor(SensorEntity):
    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name, index):
        self.hass = hass
        self.rest = rest

        self.entity_description = SensorEntityDescription(
            key='fan_speed',
            name=name,
            icon='mdi:fan',
            native_unit_of_measurement='RPM',
            state_class=SensorStateClass.MEASUREMENT
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True

        self._attr_native_value = None
        self.index = index

        self.rest.register_callback_thermals(self.update_value)

    def update_value(self, thermal: dict | None):
        if thermal:
            self._attr_native_value = thermal['Fans'][self.index]['Reading']
            self._attr_available = True
        else:
            self._attr_available = False
        self.schedule_update_ha_state()


class IdracTempSensor(SensorEntity):
    def __init__(self, hass, rest: IdracRest, device_info, unique_id, name, index):
        self.hass = hass
        self.rest = rest

        self.entity_description = SensorEntityDescription(
            key='temp',
            name=name,
            icon='mdi:thermometer',
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement='°C',
        )

        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_has_entity_name = True
        self._attr_native_value = None
        self.index = index

        self.rest.register_callback_thermals(self.update_value)

    def update_value(self, thermal: dict | None):
        if thermal:
            self._attr_native_value = thermal['Temperatures'][self.index]['ReadingCelsius']
            self._attr_available = True
        else:
            self._attr_available = False
        self.schedule_update_ha_state()
