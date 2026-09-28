"""iDRAC 6 client over the web GUI API (`/data`), also present on iDRAC 7/8.

The iDRAC web pages fetch their content from `/data?get=<keys>` (XML) after
a form login on `/data/login`. iDRAC 6 has no Redfish, so this is the only
HTTP API it offers; it needs no dependency beyond aiohttp.
"""
from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET

import aiohttp

from .client import (
    POWER_GRACEFUL_SHUTDOWN, POWER_ON, REQUEST_TIMEOUT, CannotConnect, IdracClient, IdracData, IdracInfo,
    InvalidAuth, Reading, SessionLimit, as_number,
)

_LOGGER = logging.getLogger(__name__)

# iDRAC 6 sets its session cookie (_appwebSessionId_) on the login page, not on the login POST
START_PAGE = '/start.html'
LOGIN = '/data/login'
LOGOUT = '/data/logout'
DATA = '/data'

INFO_KEYS = 'sysDesc,svcTag,hostName,fwVersion'
POLL_KEYS = 'pwState,systemLevel,powermonitordata,temperatures,fans,voltages,powerSupplies'

# <sensortype><sensorid>
SENSOR_TEMPERATURES = '1'
# systemLevel: whole-server input power, what the iDRAC 6 power page shows
SENSOR_SYSTEM_LEVEL = '3'
SENSOR_FANS = '4'
SENSOR_POWER_SUPPLIES = '8'

# /data?set=pwState:<n> (0 off, 1 on, 2 cycle, 3 reset, 4 NMI, 5 graceful shutdown)
PW_STATE_ON = 1
POWER_ACTIONS = {POWER_ON: 1, POWER_GRACEFUL_SHUTDOWN: 5}

AUTH_OK = 0
AUTH_REJECTED = 1
AUTH_SESSION_LIMIT = 5
AUTH_REJECTED_IDRAC8 = 99

# Characters the iDRAC login page escapes as "@0" + hex code (Dell's escapeStr)
ESCAPED_CHARS = '@(),:?=&#+%'


def escape_credential(value: str) -> str:
    """Encode a user name or password the way the iDRAC login page does."""
    value = value.replace('\\', '\\\\')
    return ''.join(f'@0{ord(char):X}' if char in ESCAPED_CHARS else char for char in value)


def _text(root: ET.Element, path: str) -> str | None:
    element = root.find(path)
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def _number(text: str | None) -> float | int | None:
    """First number in a text such as "168" or "228V"."""
    match = re.search(r'-?\d+(\.\d+)?', text or '')
    return as_number(match.group()) if match else None


def _status_ok(sensor: ET.Element) -> bool | None:
    """iDRAC 6 says Normal/Warning/Critical; iDRAC 7/8 give sensorHealth 2 for OK."""
    health = _text(sensor, 'sensorHealth')
    if health is not None:
        return _number(health) == 2
    status = _text(sensor, 'sensorStatus')
    if status is None or status.isdigit():
        return None
    return status.lower() == 'normal'


class IdracLegacy(IdracClient):
    api = 'legacy'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._st2: str | None = None
        self._logged_in = False
        # Set when the iDRAC has no /data API at all (iDRAC 9)
        self.missing = False
        # One session, reused across polls: an iDRAC 6 only has a handful of them
        self._lock = asyncio.Lock()

    async def _request(self, method: str, path: str, **kwargs) -> aiohttp.ClientResponse:
        try:
            response = await self.session.request(method, self.base_url + path, ssl=self.ssl_context,
                                                  timeout=REQUEST_TIMEOUT, allow_redirects=False, **kwargs)
            await response.read()
            return response
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f'{method} {path} on {self.host} failed: {err!r}') from err

    async def _login(self) -> None:
        await self._request('GET', START_PAGE)
        # Field order matters to iDRAC 6 ("user" first), hence the hand-built body
        body = f'user={escape_credential(self.username)}&password={escape_credential(self.password)}'
        response = await self._request('POST', LOGIN, data=body.encode(),
                                       headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if response.status == 404:
            self.missing = True
            raise CannotConnect(f'{self.host} has no /data web API')
        if response.status != 200:
            raise CannotConnect(f'Login on {self.host} returned HTTP {response.status}')
        text = await response.text()
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise CannotConnect(f'Unexpected login answer from {self.host}: {text[:200]!r}') from err

        result = _number(_text(root, 'authResult'))
        if result == AUTH_SESSION_LIMIT:
            raise SessionLimit(f'{self.host} has no free session left')
        if result in (AUTH_REJECTED, AUTH_REJECTED_IDRAC8):
            if blocking := _number(_text(root, 'blockingTime')):
                raise CannotConnect(f'{self.host} blocks logins from this address for {blocking} s '
                                    'after too many failures')
            raise InvalidAuth()
        if result != AUTH_OK:
            raise CannotConnect(f'Login on {self.host} refused (authResult {result}, '
                                f'{_text(root, "errorMsg")})')
        # iDRAC 7/8 and iDRAC 6 firmware 2.92 hand out an anti-CSRF token that
        # later requests must echo in an ST2 header.
        match = re.search(r'ST2=([0-9a-fA-F]+)', _text(root, 'forwardUrl') or '')
        self._st2 = match.group(1) if match else None
        self._logged_in = True
        _LOGGER.debug('Logged in to %s (ST2 token: %s)', self.host, 'yes' if self._st2 else 'no')

    def _headers(self) -> dict[str, str]:
        return {'ST2': self._st2} if self._st2 else {}

    async def _query(self, params: dict[str, str]) -> ET.Element:
        """Run a /data request in the current session, logging in (again) when needed."""
        async with self._lock:
            for attempt in (1, 2):
                if not self._logged_in:
                    await self._login()
                response = await self._request('POST', DATA, params=params, headers=self._headers())
                text = await response.text()
                if response.status == 200 and '<root' in text:
                    try:
                        root = ET.fromstring(text)
                    except ET.ParseError as err:
                        raise CannotConnect(f'Unparsable answer from {self.host}: {text[:200]!r}') from err
                    if _text(root, 'status') != 'fail' or attempt == 2:
                        return root
                # Session timed out, or the iDRAC rebooted: log in again, once
                _LOGGER.debug('Session on %s rejected (HTTP %s), logging in again', self.host, response.status)
                self._logged_in = False
            raise CannotConnect(f'{self.host} keeps rejecting the session')

    async def get_info(self) -> IdracInfo:
        root = await self._query({'get': INFO_KEYS})
        model = _text(root, 'sysDesc') or 'PowerEdge'
        return IdracInfo(
            name=_text(root, 'hostName') or model,
            manufacturer='Dell Inc.',
            model=model,
            serial=_text(root, 'svcTag') or self.host,
            firmware=_text(root, 'fwVersion'),
        )

    async def fetch(self) -> IdracData:
        return parse_poll(await self._query({'get': POLL_KEYS}))

    async def fetch_energy(self) -> float | None:
        """Cumulative energy only; used by the Redfish client on iDRAC 7/8."""
        root = await self._query({'get': 'powermonitordata'})
        return _number(_text(root, './/cumReading/totalUsage') or _text(root, './/ptsReadingc1'))

    async def set_power(self, action: str) -> None:
        root = await self._query({'set': f'pwState:{POWER_ACTIONS[action]}'})
        if _text(root, 'status') == 'fail':
            raise CannotConnect(f'{self.host} refused {action}')
        _LOGGER.info('%s sent to %s', action, self.host)

    async def close(self) -> None:
        """Free the session: an iDRAC 6 runs out of them quickly."""
        if not self._logged_in:
            return
        self._logged_in = False
        try:
            await self._request('GET', LOGOUT, headers=self._headers())
        except CannotConnect as err:
            _LOGGER.debug('Logout from %s failed: %s', self.host, err)


def parse_poll(root: ET.Element) -> IdracData:
    """Turn a /data?get=POLL_KEYS answer into IdracData."""
    data = IdracData()

    pw_state = _number(_text(root, './/pwState'))
    data.power_on = None if pw_state is None else pw_state == PW_STATE_ON

    # iDRAC 7/8 layout; iDRAC 6 instead has flat pts*/pc* fields, where
    # ipowerWatts1/pmReading only cover part of the load (122 W on a server
    # drawing 224 W), so its power comes from the systemLevel sensor below.
    data.power_watts = _number(_text(root, './/powermonitordata/presentReading/reading/reading'))
    data.energy_kwh = _number(_text(root, './/powermonitordata/cumReading/totalUsage')
                              or _text(root, './/powermonitordata/ptsReadingc1'))

    statuses: list[bool | None] = []
    for sensor_type in root.iter('sensortype'):
        kind = _text(sensor_type, 'sensorid')
        for sensor in sensor_type.iter('sensor'):
            statuses.append(_status_ok(sensor))
            if kind == SENSOR_POWER_SUPPLIES:
                # iDRAC 6 names PSUs by <location>, iDRAC 7/8 by <name>
                name = _text(sensor, 'name') or _text(sensor, 'location')
                if name:
                    data.power_supplies[name] = (name, _status_ok(sensor))
                continue
            name = _text(sensor, 'name')
            if not name:
                continue
            if kind == SENSOR_SYSTEM_LEVEL:
                data.power_watts = _number(_text(sensor, 'reading'))
            elif kind == SENSOR_TEMPERATURES:
                data.temperatures[name] = Reading(name, _number(_text(sensor, 'reading')))
            elif kind == SENSOR_FANS:
                data.fans[name] = Reading(name, _number(_text(sensor, 'reading')))

    # No single health key on iDRAC 6: unhealthy when any sensor is Warning/Critical
    known = [status for status in statuses if status is not None]
    data.health_ok = all(known) if known else None
    if data.power_watts is None:
        # No systemLevel sensor while the host is off: the one-minute average
        # still measures what the server draws in standby (26 W on an R710).
        data.power_watts = _number(_text(root, './/powermonitordata/pcAveLm'))
    return data
