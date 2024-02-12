from datetime import timedelta

DOMAIN = 'idrac_power'

DATA_IDRAC_REST_CLIENT = 'client'

HOST = 'host'
USERNAME = 'username'
PASSWORD = 'password'
CONF_INTERVAL = 'interval'
CONF_INTERVAL_DEFAULT = 300

JSON_MANUFACTURER = 'Manufacturer'
JSON_MODEL = 'Model'
JSON_NAME = 'Name'
JSON_SERIAL_NUMBER = 'SerialNumber'
JSON_FIRMWARE_VERSION = 'FirmwareVersion'
JSON_POWER_CONSUMED_WATTS = 'PowerConsumedWatts'
JSON_STATUS = "Status"
JSON_STATUS_STATE = "State"

SCAN_INTERVAL = timedelta(seconds=5)
