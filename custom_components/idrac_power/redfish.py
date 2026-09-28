"""iDRAC 7/8/9 client over the Redfish API."""
from __future__ import annotations

import base64
import logging

import aiohttp

from .client import (
    REQUEST_TIMEOUT,
    CannotConnect,
    IdracClient,
    IdracData,
    IdracInfo,
    InvalidAuth,
    Reading,
    RedfishConfig,
    SessionLimit,
    as_number,
)
from .legacy import IdracLegacy

_LOGGER = logging.getLogger(__name__)

SERVICE_ROOT = '/redfish/v1'
MANAGER = '/redfish/v1/Managers/iDRAC.Embedded.1'
SYSTEM = '/redfish/v1/Systems/System.Embedded.1'
RESET = '/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset'
CHASSIS = '/redfish/v1/Chassis/System.Embedded.1'
POWER = CHASSIS + '/Power'
THERMAL = CHASSIS + '/Thermal'
# Newer schema, the only one left on recent iDRAC 9 firmware
ENVIRONMENT_METRICS = CHASSIS + '/EnvironmentMetrics'
POWER_SUPPLIES = CHASSIS + '/PowerSubsystem/PowerSupplies'
FANS = CHASSIS + '/ThermalSubsystem/Fans'
SENSORS = CHASSIS + '/Sensors'
EXPAND = '?$expand=*($levels=1)'

# iDRAC 9 proprietary web API, used only for the cumulative energy counter
SYSMGMT_SESSION = '/sysmgmt/2015/bmc/session'
SYSMGMT_POWER = '/sysmgmt/2015/server/sensor/power'

ENERGY_REDFISH = 'redfish'
ENERGY_DATA = 'data'
ENERGY_SYSMGMT = 'sysmgmt'


def health_ok(status: dict | None) -> bool | None:
    """Map a Redfish Status object to healthy / unhealthy / unknown."""
    if not status:
        return None
    health = status.get('HealthRollup') or status.get('Health')
    if health is None:
        return None
    return health == 'OK'


class IdracRedfish(IdracClient):
    api = 'redfish'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        credentials = base64.b64encode(f'{self.username}:{self.password}'.encode()).decode()
        self._auth_header = {'Authorization': f'Basic {credentials}'}
        # Decided on the first poll that finds data, so entity ids stay stable
        self._thermal_source: str | None = None
        # Sources that answered 404 once are never asked again
        self._energy_unsupported: set[str] = set()
        self._energy_source: str | None = None
        self._data_api: IdracLegacy | None = None

    async def _request(self, method: str, path: str, **kwargs) -> aiohttp.ClientResponse:
        try:
            response = await self.session.request(
                method, self.base_url + path, headers=self._auth_header, ssl=self.ssl_context,
                timeout=REQUEST_TIMEOUT, **kwargs
            )
            await response.read()
            return response
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f'{method} {path} on {self.host} failed: {err!r}') from err

    async def get_json(self, path: str) -> dict | None:
        """GET a Redfish resource; None when the resource does not exist."""
        response = await self._request('GET', path)
        if response.status in (401, 403):
            raise InvalidAuth()
        if response.status == 404:
            await self._raise_if_redfish_disabled(response)
            return None
        if response.status != 200:
            raise CannotConnect(f'GET {path} on {self.host} returned HTTP {response.status}')
        try:
            return await response.json(content_type=None)
        except ValueError as err:
            raise CannotConnect(f'GET {path} on {self.host} returned invalid JSON') from err

    @staticmethod
    async def _raise_if_redfish_disabled(response: aiohttp.ClientResponse) -> None:
        try:
            error = (await response.json(content_type=None))['error']
            message = error['@Message.ExtendedInfo'][0]['Message']
        except (ValueError, KeyError, IndexError, TypeError):
            return
        if 'RedFish attribute is disabled' in message:
            raise RedfishConfig()

    async def get_info(self) -> IdracInfo:
        chassis = await self.get_json(CHASSIS)
        if chassis is None:
            raise CannotConnect(f'{self.host} has no Redfish chassis resource')
        firmware = None
        try:
            manager = await self.get_json(MANAGER)
            firmware = manager.get('FirmwareVersion') if manager else None
        except CannotConnect as err:
            _LOGGER.debug('Could not read the firmware version of %s: %s', self.host, err)
        return IdracInfo(
            name=chassis.get('Name') or '',
            manufacturer=chassis.get('Manufacturer') or 'Dell Inc.',
            model=chassis.get('Model') or 'PowerEdge',
            serial=chassis.get('SerialNumber') or chassis.get('SKU') or self.host,
            firmware=firmware,
        )

    async def fetch(self) -> IdracData:
        data = IdracData()

        # The system resource tells whether the iDRAC is reachable at all:
        # its failure makes the whole poll fail.
        system = await self.get_json(SYSTEM) or {}
        power_state = system.get('PowerState')
        data.power_on = None if power_state is None else power_state == 'On'
        data.health_ok = health_ok(system.get('Status'))

        for step in (self._fetch_power, self._fetch_thermals, self._fetch_energy):
            try:
                await step(data)
            except InvalidAuth:
                raise
            except (CannotConnect, RedfishConfig) as err:
                _LOGGER.debug('%s on %s failed: %s', step.__name__, self.host, err)
        return data

    async def _fetch_power(self, data: IdracData) -> None:
        power = await self.get_json(POWER)
        if power is not None:
            controls = power.get('PowerControl') or [{}]
            data.power_watts = as_number(controls[0].get('PowerConsumedWatts'))
            metrics = controls[0].get('PowerMetrics') or {}
            if (energy := as_number(metrics.get('EnergyConsumedKWh'))) is not None:
                data.energy_kwh = energy
            supplies = power.get('PowerSupplies') or []
        else:
            metrics = await self.get_json(ENVIRONMENT_METRICS) or {}
            data.power_watts = as_number((metrics.get('PowerWatts') or {}).get('Reading'))
            if (energy := as_number((metrics.get('EnergykWh') or {}).get('Reading'))) is not None:
                data.energy_kwh = energy
            supplies = await self._members(POWER_SUPPLIES)

        for index, supply in enumerate(supplies):
            status = supply.get('Status') or {}
            if status.get('State') == 'Absent':
                continue
            psu_id = str(supply.get('MemberId') or supply.get('Id') or index)
            data.power_supplies[psu_id] = (supply.get('Name') or f'PSU {index + 1}', health_ok(status))

    async def _members(self, collection_path: str, keep=lambda member_id: True) -> list[dict]:
        """Return the members of a collection, expanded in one request when supported."""
        collection = await self.get_json(collection_path + EXPAND)
        if collection is None:
            collection = await self.get_json(collection_path)
        if collection is None:
            return []
        members = []
        for member in collection.get('Members', []):
            if len(member) > 1:
                members.append(member)
                continue
            # Not expanded: only a reference; odata ids are absolute paths
            path = member.get('@odata.id', '')
            if path and keep(path.rsplit('/', 1)[-1]):
                if (resource := await self.get_json(path)) is not None:
                    members.append(resource)
        return members

    async def _fetch_thermals(self, data: IdracData) -> None:
        if self._thermal_source in (None, 'thermal'):
            thermal = await self.get_json(THERMAL)
            if thermal is not None:
                for fan in thermal.get('Fans') or []:
                    fan_id = str(fan.get('MemberId'))
                    data.fans[fan_id] = Reading(fan.get('FanName') or fan.get('Name') or fan_id,
                                                as_number(fan.get('Reading')))
                for temp in thermal.get('Temperatures') or []:
                    temp_id = str(temp.get('MemberId'))
                    data.temperatures[temp_id] = Reading(temp.get('Name') or temp_id,
                                                         as_number(temp.get('ReadingCelsius')))
            if self._thermal_source == 'thermal' or (data.fans and data.temperatures):
                self._thermal_source = 'thermal'
                return

        # iDRAC 9: /Thermal is missing or incomplete, read the ThermalSubsystem
        fans: dict[str, Reading] = {}
        temperatures: dict[str, Reading] = {}
        for fan in await self._members(FANS):
            fan_id = str(fan.get('Id'))
            speed = fan.get('SpeedPercent') or {}
            fans[fan_id] = Reading(fan.get('Name') or fan_id,
                                   as_number(speed.get('SpeedRPM', speed.get('Reading'))))
        for sensor in await self._members(SENSORS, keep=lambda member_id: 'Temp' in member_id):
            if sensor.get('ReadingType') != 'Temperature':
                continue
            sensor_id = str(sensor.get('Id'))
            temperatures[sensor_id] = Reading(sensor.get('Name') or sensor_id, as_number(sensor.get('Reading')))

        if self._thermal_source == 'subsystem' or len(fans) + len(temperatures) > len(data.fans) + len(
                data.temperatures):
            data.fans, data.temperatures = fans, temperatures
            self._thermal_source = 'subsystem'
        elif data.fans or data.temperatures:
            self._thermal_source = 'thermal'

    async def _fetch_energy(self, data: IdracData) -> None:
        if data.energy_kwh is not None:
            self._energy_source = ENERGY_REDFISH
            return
        if self._energy_source == ENERGY_REDFISH:
            return
        sources = [self._energy_source] if self._energy_source else [ENERGY_DATA, ENERGY_SYSMGMT]
        for source in sources:
            if source in self._energy_unsupported:
                continue
            reader = self._energy_via_data if source == ENERGY_DATA else self._energy_via_sysmgmt
            try:
                energy = await reader()
            except (CannotConnect, InvalidAuth, SessionLimit) as err:
                _LOGGER.debug('Energy via %s on %s failed: %s', source, self.host, err)
                continue
            if energy is not None:
                self._energy_source = source
                data.energy_kwh = energy
                return

    async def _energy_via_data(self) -> float | None:
        """iDRAC 7/8 web API (removed from iDRAC 9)."""
        if self._data_api is None:
            self._data_api = IdracLegacy(self.session, self.ssl_context, self.host, self.username,
                                         self.password)
        try:
            return await self._data_api.fetch_energy()
        except CannotConnect:
            if self._data_api.missing:
                self._energy_unsupported.add(ENERGY_DATA)
                _LOGGER.debug('No /data web API on %s', self.host)
            raise

    async def _energy_via_sysmgmt(self) -> float | None:
        """iDRAC 9 web API, one short-lived session per reading."""
        credentials = {'user': self.username, 'password': self.password}
        try:
            login = await self.session.post(self.base_url + SYSMGMT_SESSION, headers=credentials,
                                            data=credentials, ssl=self.ssl_context,
                                            timeout=REQUEST_TIMEOUT)
            await login.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f'sysmgmt login on {self.host} failed: {err!r}') from err
        if login.status == 404:
            self._energy_unsupported.add(ENERGY_SYSMGMT)
            return None
        try:
            auth_result = (await login.json(content_type=None)).get('authResult')
        except ValueError:
            auth_result = None
        token = login.headers.get('XSRF-TOKEN')
        if login.status not in (200, 201) or auth_result != 0 or not token:
            raise CannotConnect(f'sysmgmt login on {self.host}: HTTP {login.status}, authResult {auth_result}')

        headers = {'XSRF-TOKEN': token}
        try:
            response = await self.session.get(self.base_url + SYSMGMT_POWER, headers=headers,
                                              ssl=self.ssl_context, timeout=REQUEST_TIMEOUT)
            if response.status != 200:
                raise CannotConnect(f'sysmgmt power on {self.host} returned HTTP {response.status}')
            root = (await response.json(content_type=None))['root']
            return as_number(root['powermonitordata']['cumReading']['totalUsage'])
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as err:
            raise CannotConnect(f'sysmgmt power on {self.host} failed: {err!r}') from err
        finally:
            try:
                logout = await self.session.delete(self.base_url + SYSMGMT_SESSION, headers=headers,
                                                   ssl=self.ssl_context, timeout=REQUEST_TIMEOUT)
                logout.release()
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug('sysmgmt logout on %s failed: %s', self.host, err)

    async def set_power(self, action: str) -> None:
        response = await self._request('POST', RESET, json={'ResetType': action})
        if response.status in (401, 403):
            raise InvalidAuth()
        if response.status == 409:
            # Already in the requested state (e.g. "On" on a running server)
            _LOGGER.info('%s on %s: nothing to do (%s)', action, self.host, await response.text())
            return
        if response.status == 404:
            await self._raise_if_redfish_disabled(response)
        if response.status >= 300:
            raise CannotConnect(f'{action} on {self.host} returned HTTP {response.status}: {await response.text()}')
        _LOGGER.info('%s sent to %s', action, self.host)

    async def close(self) -> None:
        if self._data_api is not None:
            await self._data_api.close()
