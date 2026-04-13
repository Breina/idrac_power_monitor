import logging
import time
import re
import xml.etree.ElementTree as ET
from typing import Callable

import requests
import urllib3
from homeassistant.exceptions import HomeAssistantError
from requests import Response
from requests.exceptions import RequestException, JSONDecodeError, HTTPError
from .const import (
    JSON_NAME, JSON_MANUFACTURER, JSON_MODEL, JSON_SERIAL_NUMBER,
    JSON_POWER_CONSUMED_WATTS, JSON_FIRMWARE_VERSION, JSON_STATUS, JSON_STATUS_STATE,
    JSON_POWER_METRICS, JSON_ENERGY_CONSUMED_KWH
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LOGGER = logging.getLogger(__name__)

protocol = 'https://'
drac_managers_path = '/redfish/v1/Managers/iDRAC.Embedded.1'
drac_chassis_path = '/redfish/v1/Chassis/System.Embedded.1'
drac_powercontrol_path = '/redfish/v1/Chassis/System.Embedded.1/Power/PowerControl'
drac_reset_path = '/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset'
drac_thermals = '/redfish/v1/Chassis/System.Embedded.1/Thermal'


def handle_error(result):
    if result.status_code == 401:
        raise InvalidAuth()

    if result.status_code == 404:
        try:
            error = result.json()['error']
        except JSONDecodeError:
            # start of iDRAC can cause 404 error, ignore it
            raise CannotConnect(f"iDRAC responed with 404, but no JSON present:\n{result.text}")
        if error['code'] == 'Base.1.0.GeneralError' and 'RedFish attribute is disabled' in \
                error['@Message.ExtendedInfo'][0]['Message']:
            raise RedfishConfig()

    if result.status_code != 200:
        raise CannotConnect(result.text)


class IdracRest:
    def __init__(self, host, username, password, interval):
        self.host = host
        self.auth = (username, password)
        self.interval = interval

        self.callback_thermals: list[Callable[[dict | None], None]] = []
        self.callback_status: list[Callable[[bool | None], None]] = []
        self.callback_power_usage: list[Callable[[int | None], None]] = []
        self.callback_energy_consumption: list[Callable[[float | None], None]] = []

        self.thermal_values: dict = {}
        self.status: bool = False
        self.power_usage: int = 0
        self.energy_consumption: float = 0

        self._firmware_version: str | None = None
        self._legacy_endpoint_supported: bool = True

    def configure_firmware(self, firmware_version: str) -> None:
        """Configure client capabilities based on detected firmware version."""
        self._firmware_version = firmware_version
        try:
            major_version = int(firmware_version.split('.')[0])
            # iDRAC 9 firmware 7.x+ removed the legacy /data endpoint
            self._legacy_endpoint_supported = major_version < 7
            if not self._legacy_endpoint_supported:
                _LOGGER.info(
                    "Firmware %s detected on %s - legacy /data endpoint disabled, using Redfish only",
                    firmware_version, self.host
                )
        except (ValueError, IndexError):
            _LOGGER.warning(
                "Could not parse firmware version '%s' on %s, legacy endpoint remains enabled",
                firmware_version, self.host
            )
            self._legacy_endpoint_supported = True

    def get_device_info(self) -> dict | None:
        try:
            result = self.get_path(drac_chassis_path)
        except RequestException:
            raise CannotConnect(f"Cannot connect to {self.host}")

        handle_error(result)

        chassis_results = result.json()
        return {
            JSON_NAME: chassis_results[JSON_NAME],
            JSON_MANUFACTURER: chassis_results[JSON_MANUFACTURER],
            JSON_MODEL: chassis_results[JSON_MODEL],
            JSON_SERIAL_NUMBER: chassis_results[JSON_SERIAL_NUMBER]
        }

    def get_firmware_version(self) -> str | None:
        try:
            result = self.get_path(drac_managers_path)
        except RequestException:
            raise CannotConnect(f"Could not get firmware version of {self.host}")

        handle_error(result)

        manager_results = result.json()
        return manager_results[JSON_FIRMWARE_VERSION]

    def get_path(self, path):
        return requests.get(protocol + self.host + path, auth=self.auth, verify=False, timeout=300)

    def idrac_reset(self, reset_type: str) -> Response | None:
        '''
        reset_type (str): On, ForceOff, ForceRestart, GracefulShutdown, PushPowerButton, Nmi
            Following types of reset can be performed:
                - On: Turn on the unit.
                - ForceOff: Turn off the unit immediately (nongraceful shutdown).
                - ForceRestart: Shut down immediately and nongracefully and restart the system.
                - GracefulShutdown: Shut down gracefully and power off.
                - PushPowerButton: Simulate the pressing of the physical power button on the unit
                - Nmi: Generate a diagnostic interrupt, which is usually an NMI on x86 systems,
                       to stop normal operations, complete diagnostic actions,
                       and typically, terminate all the processes running in the system.
        '''
        try:
            response = requests.post(url=f'{protocol}{self.host}{drac_reset_path}',
                                     auth=self.auth,
                                     verify=False,
                                     json={"ResetType": reset_type},
                                     timeout=300)
        except RequestException as e:
            raise CannotConnect(f"Could not perform '{reset_type}' iDRAC reset on {self.host}: {e}")

        json = None
        status_code = response.status_code
        if status_code == 204:
            json = {}
            _LOGGER.info(f"Sucessfully performed '{reset_type}' iDRAC reset on {self.host}.")
        elif status_code == 401 or status_code == 403:
            raise InvalidAuth()
        elif status_code == 404:
            error = response.json()['error']
            if error.get('code') == 'Base.1.0.GeneralError' and 'RedFish attribute is disabled' in \
                    error['@Message.ExtendedInfo'][0]['Message']:
                raise RedfishConfig()
            raise HTTPError()
        elif status_code == 409:  # A 409 will be returned if you try to turn On a running server.
            json = {}
            _LOGGER.info(f"Could not perform '{reset_type}' iDRAC reset on {self.host}: {response.text}")
        elif status_code >= 400:
            _LOGGER.error(f"Could not perform '{reset_type}' iDRAC reset on {self.host}: {response.text}")

        if json is None:
            try:
                json = response.json()
            except JSONDecodeError:
                _LOGGER.warning(f'JSONDecodeError {status_code} {response.text}')

        if "error" in json:
            error_message = json["error"]["@Message.ExtendedInfo"][0]["Message"]
            _LOGGER.error(f"iDRAC '{reset_type}' iDRAC reset failed: {error_message}")

        return response

    def register_callback_thermals(self, callback: Callable[[dict | None], None]) -> None:
        self.callback_thermals.append(callback)

    def register_callback_status(self, callback: Callable[[bool | None], None]) -> None:
        self.callback_status.append(callback)

    def register_callback_power_usage(self, callback: Callable[[int | None], None]) -> None:
        self.callback_power_usage.append(callback)
        
    def register_callback_energy_consumption(self, callback: Callable[[float | None], None]) -> None:
        """Register callback for energy consumption updates."""
        self.callback_energy_consumption.append(callback)
        
    def get_energy_consumption_via_data_endpoint(self) -> float | None:
        """Get energy consumption using the legacy /data endpoint (pre-7.x firmware)."""
        st1 = None
        st2 = None
        login_response = None
        try:
            login_url = f"{protocol}{self.host}/data/login"
            payload = {"user": self.auth[0], "password": self.auth[1]}

            login_response = requests.post(login_url, data=payload, verify=False, timeout=300)
            if login_response.status_code == 404:
                _LOGGER.info(
                    "Legacy /data endpoint not available on %s (HTTP 404) - disabling for future requests",
                    self.host
                )
                self._legacy_endpoint_supported = False
                return None
            if login_response.status_code != 200:
                _LOGGER.debug(f"Legacy login on {self.host} failed with status code: {login_response.status_code}")
                return None

            match = re.search(r'ST1=([^,]+),ST2=([^<"]+)', login_response.text)
            if not match:
                _LOGGER.debug("Failed to extract authentication tokens from %s", self.host)
                return None

            st1 = match.group(1)
            st2 = match.group(2)

            power_url = f"{protocol}{self.host}/data"
            params = {
                "get": "powermonitordata,powergraphdata,psRedundancy,pwState,"
                       "pbtEnabled,activePLPolicy,activePLimit,pwrBudgetVal,powerSupplies,pbMaxWatts"
            }
            headers = {"ST2": st2, "Content-Type": "application/x-www-form-urlencoded"}

            power_response = requests.post(
                power_url, params=params, cookies=login_response.cookies,
                headers=headers, verify=False, timeout=300
            )

            if power_response.status_code != 200:
                _LOGGER.debug(f"Legacy power data request on {self.host} failed: {power_response.status_code}")
                return None

            try:
                root = ET.fromstring(power_response.text)
                total_usage_elem = root.find('.//totalUsage')
                if total_usage_elem is not None and total_usage_elem.text:
                    return float(total_usage_elem.text)
                _LOGGER.debug("Total usage data not found in legacy response from %s", self.host)
                return None
            except ET.ParseError as e:
                _LOGGER.debug(f"Failed to parse legacy XML from {self.host}: {e}")
                return None
            except ValueError as e:
                _LOGGER.debug(f"Failed to convert total usage to float from {self.host}: {e}")
                return None

        except Exception as e:
            _LOGGER.debug(f"Error getting energy consumption via legacy endpoint on {self.host}: {e}")
            return None
        finally:
            if st1 and st2 and login_response:
                try:
                    logout_url = f"{protocol}{self.host}/data/logout"
                    requests.post(
                        logout_url, params={"ST1": st1}, headers={"ST2": st2},
                        cookies=login_response.cookies, verify=False, timeout=300
                    )
                except Exception as e:
                    _LOGGER.debug(f"Legacy logout on {self.host} failed: {e}")

    def update_thermals(self) -> dict:
        try:
            req = self.get_path(drac_thermals)
            handle_error(req)
            new_thermals = req.json()

        except (RequestException, RedfishConfig, CannotConnect) as e:
            _LOGGER.debug(f"Couldn't update {self.host} thermals: {e}")
            new_thermals = None

        if new_thermals != self.thermal_values:
            self.thermal_values = new_thermals
            for callback in self.callback_thermals:
                callback(self.thermal_values)
        return self.thermal_values

    def update_status(self):
        try:
            result = self.get_path(drac_chassis_path)
            handle_error(result)
            status_values = result.json()

            try:
                new_status = status_values[JSON_STATUS][JSON_STATUS_STATE] == 'Enabled'
            except:
                new_status = None

        except (RequestException, RedfishConfig, CannotConnect) as e:
            _LOGGER.debug(f"Couldn't update {self.host} status: {e}")
            new_status = None

        if new_status != self.status:
            self.status = new_status
            for callback in self.callback_status:
                callback(self.status)

    def update_power_usage(self):
        try:
            result = self.get_path(drac_powercontrol_path)
            handle_error(result)
            power_values = result.json()
            _LOGGER.debug(f"Power values response from {self.host}: {power_values}")
        except (RequestException, RedfishConfig, CannotConnect) as e:
            _LOGGER.debug(f"Couldn't update {self.host} power usage: {e}")
            for callback in self.callback_power_usage:
                callback(None)
            return

        try:
            new_power_usage = power_values[JSON_POWER_CONSUMED_WATTS]
            if new_power_usage != self.power_usage:
                self.power_usage = new_power_usage
                for callback in self.callback_power_usage:
                    callback(self.power_usage)
        except:
            pass

        # Try Redfish energy data first (available on newer firmware)
        energy_updated = False
        try:
            power_metrics = power_values.get(JSON_POWER_METRICS)
            if power_metrics:
                energy_kwh = power_metrics.get(JSON_ENERGY_CONSUMED_KWH)
                if energy_kwh is not None:
                    energy_value = float(energy_kwh)
                    if energy_value != self.energy_consumption:
                        self.energy_consumption = energy_value
                        for callback in self.callback_energy_consumption:
                            callback(self.energy_consumption)
                    energy_updated = True
        except (KeyError, TypeError, ValueError) as e:
            _LOGGER.debug(f"Redfish energy data not available for {self.host}: {e}")

        # Fall back to legacy /data endpoint if Redfish didn't provide energy data
        if not energy_updated and self._legacy_endpoint_supported:
            try:
                energy_value = self.get_energy_consumption_via_data_endpoint()
                if energy_value is not None and energy_value != self.energy_consumption:
                    self.energy_consumption = energy_value
                    for callback in self.callback_energy_consumption:
                        callback(self.energy_consumption)
            except Exception as e:
                _LOGGER.debug(f"Couldn't update {self.host} energy consumption via legacy endpoint: {e}")
            # Don't set callbacks to None if we just can't find the energy data


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class RedfishConfig(HomeAssistantError):
    """Error to indicate that Redfish was not properly configured"""


class IdracMock(IdracRest):
    def __init__(self, host, username, password, interval):
        super().__init__(host, username, password, interval)
        self.is_on = True

    def get_device_info(self):
        return {
            JSON_NAME: "Mock Device",
            JSON_MANUFACTURER: "Mock Manufacturer",
            JSON_MODEL: "Mock Model",
            JSON_SERIAL_NUMBER: "Mock Serial"
        }

    def get_firmware_version(self):
        return "1.0.0"

    def idrac_reset(self, reset_type: str) -> Response | None:
        if reset_type == 'On':
            self.status = True
        elif reset_type == 'GracefulShutdown':
            self.status = False

        time.sleep(3)
        self.update_status()

        return None

    def update_thermals(self) -> dict:
        new_thermals = {
            'Fans': [
                {
                    'MemberId': "MemberID 1",
                    'FanName': "First Mock Fan",
                    'Reading': 1
                },
                {
                    'MemberId': "MemberID 2",
                    'FanName': "Second Mock Fan",
                    'Reading': 2
                }
            ],
            'Temperatures': [
                {
                    'MemberId': "MemberID 3",
                    'Name': "Mock Temperature",
                    'ReadingCelsius': 10
                }
            ]
        }

        if new_thermals != self.thermal_values:
            self.thermal_values = new_thermals
            for callback in self.callback_thermals:
                callback(self.thermal_values)
        return self.thermal_values

    def update_status(self):
        for callback in self.callback_status:
            callback(self.status)

    def update_power_usage(self):
        power_values = {
            JSON_POWER_CONSUMED_WATTS: 100,
            JSON_POWER_METRICS: {
                JSON_ENERGY_CONSUMED_KWH: 42.5
            }
        }
        try:
            new_power_usage = power_values[JSON_POWER_CONSUMED_WATTS]
            if new_power_usage != self.power_usage:
                self.power_usage = new_power_usage
                for callback in self.callback_power_usage:
                    callback(self.power_usage)
                    
            # Add energy consumption for mock
            new_energy_consumption = power_values[JSON_POWER_METRICS][JSON_ENERGY_CONSUMED_KWH]
            if new_energy_consumption != self.energy_consumption:
                self.energy_consumption = new_energy_consumption
                for callback in self.callback_energy_consumption:
                    callback(self.energy_consumption)
        except:
            pass
            
    def get_energy_consumption_via_data_endpoint(self) -> float | None:
        """Mock implementation of the data endpoint energy consumption method."""
        return 42.5
