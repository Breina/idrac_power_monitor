import logging

import requests
import urllib3
from homeassistant.exceptions import HomeAssistantError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .const import (
    JSON_NAME, JSON_MANUFACTURER, JSON_MODEL, JSON_SERIAL_NUMBER,
    JSON_POWER_CONSUMED_WATTS, JSON_FIRMWARE_VERSION, JSON_STATUS, JSON_STATUS_STATE
)

_LOGGER = logging.getLogger(__name__)

protocol = 'https://'
drac_managers_path = '/redfish/v1/Managers/iDRAC.Embedded.1'
drac_chassis_path = '/redfish/v1/Chassis/System.Embedded.1'
drac_powercontrol_path = '/redfish/v1/Chassis/System.Embedded.1/Power/PowerControl'
drac_powerON_path = '/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset'
drac_thermals = '/redfish/v1/Chassis/System.Embedded.1/Thermal'


def handle_error(result):
    if result.status_code == 401:
        raise InvalidAuth()

    if result.status_code == 404:
        error = result.json()['error']
        if error['code'] == 'Base.1.0.GeneralError' and 'RedFish attribute is disabled' in \
                error['@Message.ExtendedInfo'][0]['Message']:
            raise RedfishConfig()

    if result.status_code != 200:
        raise CannotConnect(result.text)


thermals_values = None
status_values = None
power_values = None


class IdracRest:
    def __init__(self, host, username, password, interval):
        self.host = host
        self.auth = (username, password)
        self.interval = interval

    def get_device_info(self):
        result = self.get_path(drac_chassis_path)
        handle_error(result)

        chassis_results = result.json()
        return {
            JSON_NAME: chassis_results[JSON_NAME],
            JSON_MANUFACTURER: chassis_results[JSON_MANUFACTURER],
            JSON_MODEL: chassis_results[JSON_MODEL],
            JSON_SERIAL_NUMBER: chassis_results[JSON_SERIAL_NUMBER]
        }

    def get_firmware_version(self):
        result = self.get_path(drac_managers_path)
        handle_error(result)

        manager_results = result.json()
        return manager_results[JSON_FIRMWARE_VERSION]

    def get_path(self, path):
        return requests.get(protocol + self.host + path, auth=self.auth, verify=False)

    def power_on(self):
        result = requests.post(protocol + self.host + drac_powerON_path, auth=self.auth, verify=False,
                               json={"ResetType": "On"})
        json = result.json()
        if result.status_code == 401:
            raise InvalidAuth()

        if result.status_code == 404:
            error = result.json()['error']
            if error['code'] == 'Base.1.0.GeneralError' and 'RedFish attribute is disabled' in \
                    error['@Message.ExtendedInfo'][0]['Message']:
                raise RedfishConfig()
        if "error" in json:
            _LOGGER.error("Idrac power on failed: %s", json["error"]["@Message.ExtendedInfo"][0]["Message"])

        return result

    def update_thermals(self):
        global thermals_values
        req = self.get_path(drac_thermals)
        handle_error(req)
        thermals_values = req.json()
        return thermals_values

    def get_thermals(self):
        global thermals_values
        return thermals_values

    def update_status(self):
        global status_values
        result = self.get_path(drac_chassis_path)
        handle_error(result)
        status_values = result.json()
        try:
            return status_values[JSON_STATUS][JSON_STATUS_STATE] == 'Enabled'
        except:
            return False

    def get_status(self):
        global status_values
        try:
            return status_values[JSON_STATUS][JSON_STATUS_STATE] == 'Enabled'
        except:
            return False

    def update_power_usage(self):
        global power_values
        result = self.get_path(drac_powercontrol_path)
        handle_error(result)
        power_values = result.json()
        try:
            return power_values[JSON_POWER_CONSUMED_WATTS]
        except:
            return 0

    def get_power_usage(self):
        global power_values
        try:
            return power_values[JSON_POWER_CONSUMED_WATTS]
        except:
            return 0


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class RedfishConfig(HomeAssistantError):
    """Error to indicate that Redfish was not properly configured"""
