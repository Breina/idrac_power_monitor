from homeassistant.const import (
    POWER_WATT,
    DEVICE_CLASS_ENERGY
)

from homeassistant.components.sensor import (
    STATE_CLASS_MEASUREMENT,
    SensorEntityDescription,
)

DOMAIN = 'idrac_power_monitor'

DATA_IDRAC_REST_CLIENT = 'client'

HOST = 'host'
USERNAME = 'username'
PASSWORD = 'password'

JSON_MANUFACTURER = 'Manufacturer'
JSON_MODEL = 'Model'
JSON_NAME = 'Name'
JSON_SERIAL_NUMBER = 'SerialNumber'
JSON_FIRMWARE_VERSION = 'FirmwareVersion'
JSON_POWER_CONSUMED_WATTS = 'PowerConsumedWatts'

SENSOR_DESCRIPTION = SensorEntityDescription(
    key='current_power_usage',
    name='Server power usage',
    icon='mdi:server',
    native_unit_of_measurement=POWER_WATT,
    device_class=DEVICE_CLASS_ENERGY,
    state_class=STATE_CLASS_MEASUREMENT
)
