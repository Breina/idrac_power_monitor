from homeassistant.const import (
    POWER_WATT, ENERGY_WATT_HOUR, ENERGY_KILO_WATT_HOUR,
    DEVICE_CLASS_ENERGY
)
from datetime import timedelta
from homeassistant.components.sensor import (
    STATE_CLASS_MEASUREMENT, STATE_CLASS_TOTAL,
    SensorEntityDescription,
)
from homeassistant.components.binary_sensor import (
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)

from homeassistant.components.button import (
    ButtonEntityDescription,
    ButtonDeviceClass,
)
DOMAIN = 'idrac'

DATA_IDRAC_REST_CLIENT = 'client'

HOST = 'host'
USERNAME = 'username'
PASSWORD = 'password'
CONF_INTERVAL = 'interval'

JSON_MANUFACTURER = 'Manufacturer'
JSON_MODEL = 'Model'
JSON_NAME = 'Name'
JSON_SERIAL_NUMBER = 'SerialNumber'
JSON_FIRMWARE_VERSION = 'FirmwareVersion'
JSON_POWER_CONSUMED_WATTS = 'PowerConsumedWatts'
JSON_STATUS = "Status"
JSON_STATUS_STATE = "State"

SCAN_INTERVAL = timedelta(seconds=5)

CURRENT_POWER_SENSOR_DESCRIPTION = SensorEntityDescription(
    key='current_power_usage',
    name=' current power usage',
    icon='mdi:lightning-bolt',
    native_unit_of_measurement=POWER_WATT,
    # device_class=DEVICE_CLASS_ENERGY,
    state_class=STATE_CLASS_MEASUREMENT
)

FAN_SENSOR_DESCRIPTION = SensorEntityDescription(
    key='fan_speed',
    name='fan speed',
    icon='mdi:fan',
    native_unit_of_measurement='RPM',
    state_class=STATE_CLASS_MEASUREMENT
)

TEMP_SENSOR_DESCRIPTION = SensorEntityDescription(
    key='temp',
    name='temp',
    icon='mdi:thermometer',
    state_class=STATE_CLASS_MEASUREMENT,
    native_unit_of_measurement='°C',
)

STATUS_BINARY_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key='status',
    name='status',
    icon='mdi:power',
    device_class=BinarySensorDeviceClass.POWER,
)

POWER_ON_DESCRIPTION = ButtonEntityDescription(
    key='power_on',
    name='power on',
    icon='mdi:power',
    device_class=ButtonDeviceClass.UPDATE,
)

REFRESH_DESCRIPTION = ButtonEntityDescription(
    key='refresh',
    name='refresh',
    icon='mdi:refresh',
    device_class=ButtonDeviceClass.UPDATE,
)