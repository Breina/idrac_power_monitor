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

# iDRAC 9 API paths (14th+ generation servers)
drac_thermal_subsystem_fans = '/redfish/v1/Chassis/System.Embedded.1/ThermalSubsystem/Fans'
drac_sensors = '/redfish/v1/Chassis/System.Embedded.1/Sensors'

# iDRAC 9 server models (14th, 15th, 16th generation PowerEdge servers)
# These models support the new ThermalSubsystem and Sensors APIs
IDRAC9_MODELS = [
    # 14th gen rack servers
    'R240', 'R340', 'R440', 'R540', 'R640', 'R740', 'R740xd', 'R840', 'R940',
    'R6415', 'R7415', 'R7425',
    # 15th gen rack servers
    'R650', 'R750', 'R660', 'R760',
    # 14th gen tower servers
    'T140', 'T340', 'T440', 'T640',
    # 15th gen tower servers
    'T150', 'T350', 'T550',
    # 16th gen tower servers
    'T160', 'T360', 'T560',
    # Modular and specialized servers
    'M640', 'C4140', 'C6420', 'MX740c', 'XE2420', 'XE7420', 'XE7440', 'XE8545',
    'XR11', 'XR12',
    # Precision workstations
    'Precision R7920', 'Precision R7960',
]


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
        
        # Cache for version detection to avoid repeated checks
        self._idrac_version_cache: int | None = None
        self._device_model: str | None = None

    def get_device_info(self) -> dict | None:
        try:
            result = self.get_path(drac_chassis_path)
        except RequestException:
            raise CannotConnect(f"Cannot connect to {self.host}")

        handle_error(result)

        chassis_results = result.json()
        device_info = {
            JSON_NAME: chassis_results[JSON_NAME],
            JSON_MANUFACTURER: chassis_results[JSON_MANUFACTURER],
            JSON_MODEL: chassis_results[JSON_MODEL],
            JSON_SERIAL_NUMBER: chassis_results[JSON_SERIAL_NUMBER]
        }
        
        # Cache the device model for version detection
        self._device_model = chassis_results.get(JSON_MODEL, '')
        
        return device_info

    def _extract_model_base(self, model_string: str) -> str:
        """
        Extract the base model name from a full model string.
        Examples:
            "PowerEdge R740xd dbe" -> "R740xd"
            "PowerEdge R740" -> "R740"
            "Precision R7920" -> "Precision R7920"
        """
        if not model_string:
            return ''
        
        # Remove "PowerEdge" prefix if present
        model = model_string.replace('PowerEdge', '').strip()
        
        # Split by whitespace and take relevant parts
        parts = model.split()
        if not parts:
            return ''
        
        # For Precision models, keep "Precision" + model number
        if 'Precision' in model_string:
            if len(parts) >= 2:
                return f"{parts[0]} {parts[1]}"
            return model_string.strip()
        
        # For regular models, return the first part (e.g., "R740xd" from "R740xd dbe")
        return parts[0]

    def _is_idrac9_model(self, model: str) -> bool:
        """
        Check if the server model is an iDRAC 9 compatible model.
        Uses flexible matching to handle variants (e.g., R740xd2, R740xd dbe).
        """
        if not model:
            return False
        
        base_model = self._extract_model_base(model)
        _LOGGER.debug(f"Extracted base model '{base_model}' from '{model}'")
        
        # Check if the base model starts with any of our known iDRAC 9 models
        for idrac9_model in IDRAC9_MODELS:
            if base_model.startswith(idrac9_model):
                _LOGGER.debug(f"Model '{base_model}' matches iDRAC 9 model '{idrac9_model}'")
                return True
        
        return False

    def _parse_firmware_version(self, version_string: str) -> tuple[int, int, int, int]:
        """
        Parse firmware version string into components.
        Examples:
            "2.85.85.85" -> (2, 85, 85, 85)
            "6.10.30.00" -> (6, 10, 30, 0)
            "7.00.30.00" -> (7, 0, 30, 0)
        Returns tuple of (major, minor, patch, build)
        """
        try:
            parts = version_string.split('.')
            if len(parts) >= 4:
                return (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
            elif len(parts) >= 2:
                return (int(parts[0]), int(parts[1]), 0, 0)
            elif len(parts) >= 1:
                return (int(parts[0]), 0, 0, 0)
        except (ValueError, AttributeError):
            _LOGGER.warning(f"Failed to parse firmware version: {version_string}")
        
        return (0, 0, 0, 0)

    def _get_idrac_version_from_firmware(self, firmware_version: str) -> int:
        """
        Determine iDRAC version (7, 8, or 9) from firmware version string.
        - iDRAC 7/8: 2.x
        - iDRAC 9 (14th-16th gen): 3.xx to 7.00.29.xx
        - iDRAC 9 (15th-16th gen): 7.00.30.00+
        Returns 7, 8, or 9. Returns 0 if unable to determine.
        """
        major, minor, patch, build = self._parse_firmware_version(firmware_version)
        
        if major == 2:
            # iDRAC 7/8 use 2.x firmware
            return 8  # Return 8 as generic for 7/8 (they use same APIs)
        elif major >= 3 and major <= 6:
            # iDRAC 9: versions 3.x through 6.x
            return 9
        elif major == 7:
            # iDRAC 9: version 7.00.00.00 through 7.00.29.xx
            # Later versions (7.00.30+) are also iDRAC 9 but on newer hardware
            if minor == 0 and patch < 30:
                return 9
            elif minor == 0 and patch >= 30:
                return 9  # Still iDRAC 9, just newer generation
            elif minor > 0:
                return 9
        
        # Unknown version
        _LOGGER.warning(f"Unknown firmware version pattern: {firmware_version}")
        return 0

    def detect_idrac_version(self) -> int:
        """
        Detect iDRAC version using both model and firmware information.
        Returns 8 for iDRAC 7/8, or 9 for iDRAC 9.
        Returns 8 (safe default) if detection fails.
        """
        # Return cached value if available
        if self._idrac_version_cache is not None:
            return self._idrac_version_cache
        
        detected_version = 8  # Default to iDRAC 7/8 for backward compatibility
        
        try:
            # Try to get firmware version first
            firmware_version = self.get_firmware_version()
            if firmware_version:
                version_from_fw = self._get_idrac_version_from_firmware(firmware_version)
                if version_from_fw > 0:
                    detected_version = version_from_fw
                    _LOGGER.info(f"Detected iDRAC version {detected_version} from firmware: {firmware_version}")
            
            # Verify with model information if available
            if self._device_model:
                is_idrac9_model = self._is_idrac9_model(self._device_model)
                if is_idrac9_model and detected_version == 8:
                    _LOGGER.warning(
                        f"Firmware suggests iDRAC 7/8 but model '{self._device_model}' is iDRAC 9. "
                        f"Using iDRAC 9 APIs."
                    )
                    detected_version = 9
                elif not is_idrac9_model and detected_version == 9:
                    _LOGGER.warning(
                        f"Firmware suggests iDRAC 9 but model '{self._device_model}' is not recognized as iDRAC 9. "
                        f"Will try iDRAC 9 APIs with fallback."
                    )
        except Exception as e:
            _LOGGER.warning(f"Error during iDRAC version detection: {e}. Defaulting to iDRAC 7/8 mode.")
            detected_version = 8
        
        # Cache the result
        self._idrac_version_cache = detected_version
        _LOGGER.info(f"iDRAC version detection complete: Using iDRAC {detected_version} APIs for {self.host}")
        
        return detected_version

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

    def _normalize_idrac9_fans(self, fans_collection: dict) -> list[dict]:
        """
        Normalize iDRAC 9 ThermalSubsystem/Fans API response to legacy format.
        iDRAC 9 format: Collection with Members[], each fan is a separate resource
        Legacy format: Array with MemberId, FanName, Reading
        """
        normalized_fans = []
        
        try:
            members = fans_collection.get('Members', [])
            _LOGGER.debug(f"Processing {len(members)} fan resources from iDRAC 9 API")
            
            for member_ref in members:
                # Each member is a reference like {'@odata.id': '/path/to/fan'}
                # We need to fetch each fan individually
                fan_url = member_ref.get('@odata.id', '')
                if not fan_url:
                    continue
                
                try:
                    # Extract just the path from the URL
                    fan_path = fan_url.replace('/redfish/v1', '')
                    fan_response = self.get_path(fan_path)
                    handle_error(fan_response)
                    fan_data = fan_response.json()
                    
                    # Extract fan information
                    fan_id = fan_data.get('Id', '')
                    fan_name = fan_data.get('Name', fan_id)
                    
                    # iDRAC 9 stores RPM in SpeedPercent.SpeedRPM
                    speed_rpm = None
                    speed_percent = fan_data.get('SpeedPercent', {})
                    if isinstance(speed_percent, dict):
                        speed_rpm = speed_percent.get('SpeedRPM')
                    
                    if speed_rpm is not None:
                        normalized_fans.append({
                            'MemberId': fan_id,
                            'FanName': fan_name,
                            'Reading': speed_rpm
                        })
                        _LOGGER.debug(f"Normalized fan: {fan_name} = {speed_rpm} RPM")
                    
                except Exception as e:
                    _LOGGER.warning(f"Failed to fetch fan data from {fan_url}: {e}")
                    continue
                    
        except Exception as e:
            _LOGGER.error(f"Error normalizing iDRAC 9 fans: {e}")
        
        return normalized_fans

    def _normalize_idrac9_temperatures(self, sensors_collection: dict) -> list[dict]:
        """
        Normalize iDRAC 9 Sensors API response to legacy format.
        iDRAC 9 format: Collection with Members[], need to filter by ReadingType="Temperature"
        Legacy format: Array with MemberId, Name, ReadingCelsius
        """
        normalized_temps = []
        
        try:
            members = sensors_collection.get('Members', [])
            _LOGGER.debug(f"Processing {len(members)} sensor resources from iDRAC 9 API")
            
            for member_ref in members:
                # Each member is a reference like {'@odata.id': '/path/to/sensor'}
                sensor_url = member_ref.get('@odata.id', '')
                if not sensor_url:
                    continue
                
                try:
                    # Extract just the path from the URL
                    sensor_path = sensor_url.replace('/redfish/v1', '')
                    sensor_response = self.get_path(sensor_path)
                    handle_error(sensor_response)
                    sensor_data = sensor_response.json()
                    
                    # Only process temperature sensors
                    reading_type = sensor_data.get('ReadingType', '')
                    if reading_type != 'Temperature':
                        continue
                    
                    # Extract sensor information
                    sensor_id = sensor_data.get('Id', '')
                    sensor_name = sensor_data.get('Name', sensor_id)
                    reading = sensor_data.get('Reading')
                    
                    if reading is not None:
                        normalized_temps.append({
                            'MemberId': sensor_id,
                            'Name': sensor_name,
                            'ReadingCelsius': reading
                        })
                        _LOGGER.debug(f"Normalized temperature sensor: {sensor_name} = {reading}°C")
                    
                except Exception as e:
                    _LOGGER.warning(f"Failed to fetch sensor data from {sensor_url}: {e}")
                    continue
                    
        except Exception as e:
            _LOGGER.error(f"Error normalizing iDRAC 9 temperature sensors: {e}")
        
        return normalized_temps

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
        """Get energy consumption using the /data endpoint approach from idrac.py."""
        try:
            # First login to get tokens
            login_url = f"{protocol}{self.host}/data/login"
            payload = {"user": self.auth[0], "password": self.auth[1]}
            
            login_response = requests.post(login_url, data=payload, verify=False, timeout=300)
            if login_response.status_code != 200:
                _LOGGER.error(f"Login failed with status code: {login_response.status_code}")
                return None
                
            # Extract ST1 and ST2 tokens from response
            match = re.search(r'ST1=([^,]+),ST2=([^<"]+)', login_response.text)
            if not match:
                _LOGGER.error("Failed to extract authentication tokens")
                return None
                
            st1 = match.group(1)
            st2 = match.group(2)
            
            # Get power monitoring data
            power_url = f"{protocol}{self.host}/data"
            params = {
                "get": "powermonitordata,powergraphdata,psRedundancy,pwState,pbtEnabled,activePLPolicy,activePLimit,pwrBudgetVal,powerSupplies,pbMaxWatts"
            }
            
            headers = {
                "ST2": st2,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            power_response = requests.post(
                power_url, 
                params=params, 
                cookies=login_response.cookies, 
                headers=headers,
                verify=False,
                timeout=300
            )
            
            if power_response.status_code != 200:
                _LOGGER.error(f"Power data request failed with status code: {power_response.status_code}")
                return None
                
            # Parse the XML response to extract total usage
            try:
                root = ET.fromstring(power_response.text)
                total_usage_elem = root.find('.//totalUsage')
                
                if total_usage_elem is not None and total_usage_elem.text:
                    return float(total_usage_elem.text)
                else:
                    _LOGGER.debug("Total usage data not found in the response")
                    return None
            except ET.ParseError as e:
                _LOGGER.error(f"Failed to parse XML: {e}")
                return None
            except ValueError as e:
                _LOGGER.error(f"Failed to convert total usage to float: {e}")
                return None
            
        except Exception as e:
            _LOGGER.error(f"Error getting energy consumption via data endpoint: {e}")
            return None
        finally:
            # Try to logout to free the session
            try:
                logout_url = f"{protocol}{self.host}/data/logout"
                params = {"ST1": st1}
                headers = {"ST2": st2}
                requests.post(logout_url, params=params, headers=headers, cookies=login_response.cookies, verify=False, timeout=300)
            except Exception as e:
                _LOGGER.debug(f"Logout failed: {e}")

    def update_thermals(self) -> dict:
        """
        Update thermal data (fans and temperatures).
        Tries iDRAC 9 APIs first, falls back to legacy API if needed.
        """
        new_thermals = None
        idrac_version = self.detect_idrac_version()
        
        # Try iDRAC 9 APIs first if we detected iDRAC 9
        if idrac_version == 9:
            _LOGGER.debug(f"Attempting to fetch thermals using iDRAC 9 APIs for {self.host}")
            try:
                new_thermals = self._fetch_idrac9_thermals()
                if new_thermals and (new_thermals.get('Fans') or new_thermals.get('Temperatures')):
                    _LOGGER.debug(f"Successfully fetched thermals using iDRAC 9 APIs: "
                                f"{len(new_thermals.get('Fans', []))} fans, "
                                f"{len(new_thermals.get('Temperatures', []))} temps")
                else:
                    _LOGGER.info(f"iDRAC 9 APIs returned empty data, falling back to legacy API")
                    new_thermals = None
            except Exception as e:
                _LOGGER.info(f"iDRAC 9 thermal APIs failed ({e}), falling back to legacy API")
                new_thermals = None
        
        # Fall back to legacy API if iDRAC 7/8 or if iDRAC 9 APIs failed
        if new_thermals is None:
            _LOGGER.debug(f"Fetching thermals using legacy API for {self.host}")
            try:
                req = self.get_path(drac_thermals)
                handle_error(req)
                new_thermals = req.json()
                _LOGGER.debug(f"Successfully fetched thermals using legacy API")
            except (RequestException, RedfishConfig, CannotConnect) as e:
                _LOGGER.debug(f"Couldn't update {self.host} thermals: {e}")
                new_thermals = None

        if new_thermals != self.thermal_values:
            self.thermal_values = new_thermals
            for callback in self.callback_thermals:
                callback(self.thermal_values)
        return self.thermal_values

    def _fetch_idrac9_thermals(self) -> dict:
        """
        Fetch thermal data using iDRAC 9 APIs.
        Returns normalized data in legacy format for compatibility.
        """
        fans = []
        temperatures = []
        
        # Fetch fans from ThermalSubsystem/Fans
        try:
            fans_response = self.get_path(drac_thermal_subsystem_fans)
            if fans_response.status_code == 200:
                fans_collection = fans_response.json()
                fans = self._normalize_idrac9_fans(fans_collection)
                _LOGGER.debug(f"Fetched {len(fans)} fans from iDRAC 9 ThermalSubsystem API")
            else:
                _LOGGER.warning(f"iDRAC 9 fans endpoint returned status {fans_response.status_code}")
        except Exception as e:
            _LOGGER.warning(f"Failed to fetch fans from iDRAC 9 API: {e}")
        
        # Fetch temperatures from Sensors
        try:
            sensors_response = self.get_path(drac_sensors)
            if sensors_response.status_code == 200:
                sensors_collection = sensors_response.json()
                temperatures = self._normalize_idrac9_temperatures(sensors_collection)
                _LOGGER.debug(f"Fetched {len(temperatures)} temperature sensors from iDRAC 9 Sensors API")
            else:
                _LOGGER.warning(f"iDRAC 9 sensors endpoint returned status {sensors_response.status_code}")
        except Exception as e:
            _LOGGER.warning(f"Failed to fetch temperature sensors from iDRAC 9 API: {e}")
        
        # Return in legacy format
        return {
            'Fans': fans,
            'Temperatures': temperatures
        }

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
        """
        Update power consumption and energy usage data.
        Note: Energy consumption is only available on iDRAC 7/8.
        """
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
            
        # Get energy consumption - only available on iDRAC 7/8
        idrac_version = self.detect_idrac_version()
        if idrac_version == 9:
            # iDRAC 9 does not provide cumulative energy consumption via any API
            _LOGGER.debug(f"Energy consumption not available on iDRAC 9 ({self.host})")
            # Note: We don't call callbacks here to keep the sensor in its last state
            # rather than marking it unavailable on every update
        else:
            # Try the data endpoint approach for iDRAC 7/8
            try:
                energy_value = self.get_energy_consumption_via_data_endpoint()
                
                if energy_value is not None and energy_value != self.energy_consumption:
                    self.energy_consumption = energy_value
                    for callback in self.callback_energy_consumption:
                        callback(self.energy_consumption)
            except Exception as e:
                _LOGGER.debug(f"Couldn't update {self.host} energy consumption: {e}")
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
        # Mock can simulate either iDRAC 8 or 9 based on host pattern
        self._mock_idrac9 = 'idrac9' in host.lower() or 'r740' in host.lower()

    def get_device_info(self):
        # Return iDRAC 9 model if simulating iDRAC 9
        model = "PowerEdge R740" if self._mock_idrac9 else "PowerEdge R630"
        device_info = {
            JSON_NAME: "Mock Device",
            JSON_MANUFACTURER: "Dell Inc.",
            JSON_MODEL: model,
            JSON_SERIAL_NUMBER: "Mock Serial"
        }
        # Cache the model for version detection
        self._device_model = model
        return device_info

    def get_firmware_version(self):
        # Return appropriate firmware version for mock type
        return "6.10.30.00" if self._mock_idrac9 else "2.85.85.85"

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
