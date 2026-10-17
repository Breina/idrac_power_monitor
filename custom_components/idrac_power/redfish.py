"""iDRAC 7/8/9 client over the Redfish API."""
from __future__ import annotations

import base64
import json
import logging
import time

import aiohttp

from .client import (
    REQUEST_TIMEOUT,
    Alert,
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
# System Event Log: iDRAC 9 (and 7/8 on recent firmware) under LogServices,
# older iDRAC 7/8 firmware under Logs
LOG_SERVICES = MANAGER + '/LogServices'
SEL_ENTRIES = LOG_SERVICES + '/Sel/Entries'
SEL_LEGACY = MANAGER + '/Logs/Sel'
# The log is a large resource on a slow controller: no need to read it more often
ALERTS_MIN_INTERVAL = 60
# A log listed oldest first is paged through to its newest entries, up to this many pages
MAX_LOG_PAGES = 20
# Enough for a whole /Power or /Thermal answer of a 2U server
LOGGED_RESPONSE_CHARS = 30000

# iDRAC 9 proprietary web API, used only for the cumulative energy counter
SYSMGMT_SESSION = '/sysmgmt/2015/bmc/session'
SYSMGMT_POWER = '/sysmgmt/2015/server/sensor/power'

# Retry delays for an energy counter source that failed (seconds)
ENERGY_RETRY_AFTER_REFUSED = 6 * 3600
ENERGY_RETRY_AFTER_ERROR = 3600

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


def is_idrac9_firmware(firmware: str | None) -> bool:
    """iDRAC 9 firmware is 3.x and later; iDRAC 7/8 run 1.x / 2.x. Unknown: assume it might be."""
    if not firmware:
        return True
    major = firmware.split('.', 1)[0]
    return not major.isdigit() or int(major) >= 3


def plausible_psu_inputs(inputs: dict[str, float], total_watts: float | None) -> bool:
    """Whether per-PSU input readings are live values.

    iDRAC 7/8 firmware fills PowerInputWatts with the PSU's rated input
    (900 W for a 750 W supply) whatever the load: such values add up to far
    more than the whole server draws.
    """
    if not inputs or total_watts is None:
        return bool(inputs)
    return sum(inputs.values()) <= total_watts * 1.5 + 50


def parse_log_entry(entry: dict) -> Alert | None:
    """A Redfish LogEntry as an Alert; None for references or entries without a message."""
    message = (entry.get('Message') or '').strip()
    entry_id = entry.get('Id')
    if not message or entry_id in (None, ''):
        return None
    severity = entry.get('Severity') or 'OK'
    if severity not in ('OK', 'Warning', 'Critical'):
        severity = 'OK'
    sensor = entry.get('SensorNumber')
    if sensor is None:
        sensor = _member(entry.get('SensorType'))
    return Alert(
        id=str(entry_id),
        created=entry.get('Created') or '',
        severity=severity,
        message=message,
        message_id=entry.get('MessageId') or None,
        sensor=None if sensor in (None, '') else str(sensor),
        assertion=_member(entry.get('EntryCode')),
    )


def _member(value) -> str | None:
    """Dell wraps enum values as [{"Member": "Assert"}]; Redfish uses plain strings."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get('Member')
    return value if isinstance(value, str) and value else None


def _created(entry: dict) -> str:
    return entry.get('Created') or ''


def _numeric_id(value: str):
    """Sort "2" before "10"; non-numeric ids after numeric ones."""
    return (0, int(value), '') if value.isdigit() else (1, 0, value)


def stable_id(item: dict, *keys: str) -> str | None:
    """The identifier an entity's unique id is built on; None when the item has none.

    Never a list position nor "None": iDRAC 8 now and then puts entries whose
    every field is null in front of the real ones, and entities registered from
    those would never be provided again.
    """
    for key in keys:
        if value := item.get(key):
            return str(value)
    return None


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
        # Source -> monotonic time before which it is not tried again: a
        # refused login is retried every poll otherwise, which slows each poll
        # down and fills the iDRAC's log with failed logins.
        self._energy_retry_at: dict[str, float] = {}
        self._firmware: str | None = None
        self._energy_source: str | None = None
        self._data_api: IdracLegacy | None = None
        # System Event Log path once found; '' when this iDRAC has none
        self._sel_path: str | None = None
        self._alerts: list[Alert] | None = None
        self._alerts_read_at = 0.0
        # Raw answers dumped once at debug level, for issue reports
        self._dumped: set[str] = set()

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
        self._firmware = firmware
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
        self._dump_once('System status', {key: system.get(key) for key in ('Status', 'PowerState', 'Oem')})
        power_state = system.get('PowerState')
        data.power_on = None if power_state is None else power_state == 'On'
        data.health_ok = health_ok(system.get('Status'))

        for step in (self._fetch_power, self._fetch_thermals, self._fetch_energy, self._fetch_alerts):
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

        self._dump_once('Power supplies', supplies)
        if power is not None:
            self._dump_once('Power control', power.get('PowerControl'))
        skipped = 0
        for supply in supplies:
            status = supply.get('Status') or {}
            psu_id = stable_id(supply, 'MemberId', 'Id')
            if psu_id is None:
                skipped += 1
                continue
            if status.get('State') == 'Absent':
                continue
            data.power_supplies[psu_id] = (supply.get('Name') or psu_id, health_ok(status))
            if (watts := await self._psu_input_watts(supply)) is not None:
                data.psu_input_watts[psu_id] = watts
        if not plausible_psu_inputs(data.psu_input_watts, data.power_watts):
            _LOGGER.debug('Per-PSU input power on %s (%s) exceeds the server total (%s W): not live readings',
                          self.host, data.psu_input_watts, data.power_watts)
            data.psu_input_watts = {}
        self._log_unidentified(POWER if power is not None else POWER_SUPPLIES, {'power supplies': skipped},
                                power if power is not None else supplies)

    def _dump_once(self, what: str, payload) -> None:
        """Log a raw iDRAC answer once per setup at debug level (what each firmware actually sends)."""
        if what in self._dumped or not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        self._dumped.add(what)
        _LOGGER.debug('%s on %s: %s', what, self.host, json.dumps(payload, default=str)[:LOGGED_RESPONSE_CHARS])

    async def _psu_input_watts(self, supply: dict) -> float | None:
        """Input power of one PSU: /Power lists it inline, PowerSubsystem behind a Metrics link."""
        if (watts := as_number(supply.get('PowerInputWatts'))) is not None:
            return watts
        metrics_path = (supply.get('Metrics') or {}).get('@odata.id')
        if not metrics_path:
            return None
        metrics = await self.get_json(metrics_path) or {}
        return as_number((metrics.get('InputPowerWatts') or {}).get('Reading'))

    async def _fetch_alerts(self, data: IdracData) -> None:
        """System Event Log entries, oldest first. Read at most once a minute."""
        if self._sel_path == '':
            return
        if self._alerts is not None and time.monotonic() - self._alerts_read_at < ALERTS_MIN_INTERVAL:
            data.alerts = self._alerts
            return
        if self._sel_path is None:
            self._sel_path = await self._find_sel()
            if not self._sel_path:
                _LOGGER.debug('No readable System Event Log on %s', self.host)
                return
        log = await self.get_json(self._sel_path)
        if log is None:
            return
        members = log.get('Members') or []
        self._dump_once('System Event Log', {'path': self._sel_path, 'count': len(members),
                                             'keys': sorted(log), 'first': members[:2], 'last': members[-3:]})
        members = await self._newest_log_page(log, members)
        alerts = []
        for entry in members:
            alert = parse_log_entry(entry)
            if alert is not None:
                alerts.append(alert)
        alerts.sort(key=lambda alert: (alert.created, _numeric_id(alert.id)))
        self._alerts = data.alerts = alerts
        self._alerts_read_at = time.monotonic()

    async def _newest_log_page(self, log: dict, members: list[dict]) -> list[dict]:
        """Entries of the page holding the newest ones.

        iDRAC 7/8 list the log newest first, 50 per page: the first page is
        the one that matters. A log listed oldest first is followed to its
        last page.
        """
        next_link = log.get('Members@odata.nextLink')
        if not next_link or len(members) < 2 or _created(members[0]) >= _created(members[-1]):
            return members
        for _ in range(MAX_LOG_PAGES):
            page = await self.get_json(next_link)
            if not page or not page.get('Members'):
                break
            members = page['Members']
            next_link = page.get('Members@odata.nextLink')
            if not next_link:
                break
        return members

    async def _find_sel(self) -> str:
        """Path of the SEL entry collection.

        The Sel log service links to its entries: LogServices/Sel/Entries on
        iDRAC 9, Logs/Sel on iDRAC 7/8 (where LogServices/Sel/Entries answers
        with the log service itself).
        """
        services = await self.get_json(LOG_SERVICES)
        self._dump_once('Log services', services)
        if services is not None:
            paths = {member.get('@odata.id', '').rstrip('/').rsplit('/', 1)[-1]: member.get('@odata.id')
                     for member in services.get('Members', [])}
            if 'Sel' in paths:
                service = await self.get_json(paths['Sel']) or {}
                return (service.get('Entries') or {}).get('@odata.id') or SEL_ENTRIES
        if await self.get_json(SEL_LEGACY) is not None:
            return SEL_LEGACY
        return ''

    def _log_unidentified(self, path: str, skipped: dict[str, int], response) -> None:
        """Log an answer whose items had no identifier, to find out what the iDRAC sent."""
        if not any(skipped.values()) or not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        counts = ', '.join(f'{count} {kind}' for kind, count in skipped.items() if count)
        _LOGGER.debug('%s on %s returned items without identifiers, skipped: %s. Response: %s',
                      path, self.host, counts, json.dumps(response, default=str)[:LOGGED_RESPONSE_CHARS])

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
                skipped = {'fans': 0, 'temperatures': 0}
                for fan in thermal.get('Fans') or []:
                    if (fan_id := stable_id(fan, 'MemberId')) is None:
                        skipped['fans'] += 1
                        continue
                    data.fans[fan_id] = Reading(fan.get('FanName') or fan.get('Name') or fan_id,
                                                as_number(fan.get('Reading')))
                for temp in thermal.get('Temperatures') or []:
                    if (temp_id := stable_id(temp, 'MemberId')) is None:
                        skipped['temperatures'] += 1
                        continue
                    data.temperatures[temp_id] = Reading(temp.get('Name') or temp_id,
                                                         as_number(temp.get('ReadingCelsius')))
                self._log_unidentified(THERMAL, skipped, thermal)
            if self._thermal_source == 'thermal' or (data.fans and data.temperatures):
                self._thermal_source = 'thermal'
                return

        # iDRAC 9: /Thermal is missing or incomplete, read the ThermalSubsystem
        fans: dict[str, Reading] = {}
        temperatures: dict[str, Reading] = {}
        fan_members = await self._members(FANS)
        skipped = 0
        for fan in fan_members:
            if (fan_id := stable_id(fan, 'Id')) is None:
                skipped += 1
                continue
            speed = fan.get('SpeedPercent') or {}
            fans[fan_id] = Reading(fan.get('Name') or fan_id,
                                   as_number(speed.get('SpeedRPM', speed.get('Reading'))))
        self._log_unidentified(FANS, {'fans': skipped}, fan_members)
        sensors = await self._members(SENSORS, keep=lambda member_id: 'Temp' in member_id)
        skipped = 0
        for sensor in sensors:
            if (sensor_id := stable_id(sensor, 'Id')) is None:
                skipped += 1
                continue
            if sensor.get('ReadingType') != 'Temperature':
                continue
            temperatures[sensor_id] = Reading(sensor.get('Name') or sensor_id, as_number(sensor.get('Reading')))
        self._log_unidentified(SENSORS, {'sensors': skipped}, sensors)

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
        if not is_idrac9_firmware(self._firmware):
            # The sysmgmt API only exists on iDRAC 9
            sources = [source for source in sources if source != ENERGY_SYSMGMT]
        now = time.monotonic()
        for source in sources:
            if source in self._energy_unsupported or now < self._energy_retry_at.get(source, 0):
                continue
            reader = self._energy_via_data if source == ENERGY_DATA else self._energy_via_sysmgmt
            try:
                energy = await reader()
            except (CannotConnect, InvalidAuth, SessionLimit) as err:
                delay = ENERGY_RETRY_AFTER_REFUSED if isinstance(err, InvalidAuth) else ENERGY_RETRY_AFTER_ERROR
                self._energy_retry_at[source] = now + delay
                _LOGGER.debug('Energy via %s on %s failed (%s), next try in %d min',
                              source, self.host, repr(err), delay // 60)
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
