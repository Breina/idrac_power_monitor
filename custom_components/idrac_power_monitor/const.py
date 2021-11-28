from homeassistant.const import (
    POWER_WATT, ENERGY_WATT_HOUR,
    DEVICE_CLASS_ENERGY
)

from homeassistant.components.sensor import (
    STATE_CLASS_MEASUREMENT, STATE_CLASS_TOTAL,
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

CURRENT_POWER_SENSOR_DESCRIPTION = SensorEntityDescription(
    key='current_power_usage',
    name=' current power usage',
    icon='mdi:server',
    native_unit_of_measurement=POWER_WATT,
    device_class=DEVICE_CLASS_ENERGY,
    state_class=STATE_CLASS_MEASUREMENT
)

TOTAL_POWER_SENSOR_DESCRIPTION = SensorEntityDescription(
    key='total_power_usage',
    name=' total power usage',
    icon='mdi:server',
    native_unit_of_measurement=ENERGY_WATT_HOUR,
    device_class=DEVICE_CLASS_ENERGY,
    state_class=STATE_CLASS_TOTAL
)
