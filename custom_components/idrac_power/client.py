"""Backend-agnostic iDRAC client interface, data model and errors."""
from __future__ import annotations

import re
import ssl
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp
from homeassistant.exceptions import HomeAssistantError

# iDRAC answers are slow (an iDRAC6 regularly needs several seconds per page),
# but a request that has not completed within this delay is a dead one.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=45)

# Redfish ResetType names; the iDRAC 6 client maps them to its pwState codes.
POWER_ON = 'On'
POWER_GRACEFUL_SHUTDOWN = 'GracefulShutdown'
# Cut the power / hard reset without asking the operating system: for a server
# that is hung, or has no OS to answer the shutdown request.
POWER_FORCE_OFF = 'ForceOff'
POWER_FORCE_RESTART = 'ForceRestart'


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class RedfishConfig(HomeAssistantError):
    """Error to indicate that Redfish was not properly configured."""


class SessionLimit(HomeAssistantError):
    """Error to indicate the iDRAC refused a login because all its sessions are in use."""


@dataclass
class IdracInfo:
    """Static identity of the managed server."""
    name: str
    manufacturer: str
    model: str
    serial: str
    firmware: str | None = None


@dataclass
class Reading:
    """A named sensor value; `value` is None when the sensor has no reading."""
    name: str
    value: float | None


# Event log severities, the same whatever the iDRAC calls them
SEVERITY_OK = 'ok'
SEVERITY_WARNING = 'warning'
SEVERITY_CRITICAL = 'critical'


def normalize_severity(value: str | None) -> str | None:
    """iDRAC 6 says Normal/Warning/Critical, Redfish OK/Warning/Critical."""
    value = (value or '').strip().lower()
    if value in ('normal', 'ok', 'info', 'informational'):
        return SEVERITY_OK
    if value in (SEVERITY_WARNING, SEVERITY_CRITICAL):
        return value
    return None


@dataclass(frozen=True)
class LogEntry:
    """One System Event Log record. `created` is None for "System Boot" records (clock not set yet)."""
    severity: str | None
    created: datetime | None
    message: str


@dataclass
class PowerSupply:
    """One power supply, with what the iDRAC measures of it.

    `load` is a per-PSU reading (input current on iDRAC 6, input watts on
    Redfish) used only for how the server's measured power is spread between
    PSUs. A `has_*` flag says the iDRAC reports that reading at all; the value
    is None while it has none (host off).
    """
    name: str
    healthy: bool | None
    current: float | None = None
    has_current: bool = False
    load: float | None = None
    has_load: bool = False

    @property
    def label(self) -> str:
        """Short name for per-PSU entities: Redfish calls PSUs "PS1 Status"."""
        return re.sub(r'\s+status$', '', self.name, flags=re.IGNORECASE) or self.name


@dataclass
class IdracData:
    """One poll's worth of data. None means "not available on this iDRAC"."""
    power_on: bool | None = None
    power_watts: float | None = None
    energy_kwh: float | None = None
    health_ok: bool | None = None
    fans: dict[str, Reading] = field(default_factory=dict)
    temperatures: dict[str, Reading] = field(default_factory=dict)
    power_supplies: dict[str, PowerSupply] = field(default_factory=dict)
    # The PSU redundancy sensor, which is not a PSU: whether the iDRAC has one,
    # and whether redundancy is ok (None while unknown, e.g. host off)
    psu_redundancy_listed: bool = False
    psu_redundancy_ok: bool | None = None
    # Set by the coordinator: each PSU's share of power_watts, and of the
    # energy counter's increases since the integration first saw it
    psu_power: dict[str, float | None] = field(default_factory=dict)
    psu_energy: dict[str, float] = field(default_factory=dict)
    # System Event Log, oldest first; None when this iDRAC does not provide it
    events: list[LogEntry] | None = None


def as_number(value) -> float | int | None:
    """Parse an iDRAC reading, keeping whole numbers as int (112 W, not 112.0 W)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def create_ssl_context() -> ssl.SSLContext:
    """Build a TLS context that still talks to old iDRAC firmware.

    iDRAC certificates are self-signed, and iDRAC6/7 firmware only offers old
    protocol versions, small keys and ciphers that OpenSSL 3 refuses at its
    default security level.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with warnings.catch_warnings():
            # Deprecated, and needed: iDRAC 6 firmware predates TLS 1.2
            warnings.simplefilter('ignore', DeprecationWarning)
            context.minimum_version = ssl.TLSVersion.TLSv1
    except (ValueError, ssl.SSLError):
        pass
    try:
        context.set_ciphers('DEFAULT:@SECLEVEL=0')
    except ssl.SSLError:
        pass
    # OpenSSL 3 rejects servers without RFC 5746 secure renegotiation
    # ("unsafe legacy renegotiation disabled"), which old iDRAC firmware lacks.
    context.options |= getattr(ssl, 'OP_LEGACY_SERVER_CONNECT', 0)
    return context


def normalize_host(host: str) -> str:
    """Accept what users paste: strip the scheme, path and surrounding blanks."""
    host = host.strip()
    for prefix in ('https://', 'http://'):
        if host.lower().startswith(prefix):
            host = host[len(prefix):]
    return host.split('/', 1)[0]


class IdracClient(ABC):
    """What the integration needs from an iDRAC, whatever API it speaks."""

    api: str

    def __init__(self, session: aiohttp.ClientSession, ssl_context: ssl.SSLContext | None,
                 host: str, username: str, password: str):
        self.session = session
        self.ssl_context = ssl_context
        self.host = normalize_host(host)
        self.username = username
        self.password = password

    @property
    def base_url(self) -> str:
        return f'https://{self.host}'

    @abstractmethod
    async def get_info(self) -> IdracInfo:
        """Identify the server. Raises CannotConnect / InvalidAuth / RedfishConfig."""

    @abstractmethod
    async def fetch(self) -> IdracData:
        """Poll every sensor. Raises CannotConnect / InvalidAuth when the iDRAC is unusable."""

    async def fetch_events(self) -> list[LogEntry] | None:
        """The System Event Log, oldest first, or None when this API does not expose it."""
        return None

    async def clear_events(self) -> None:
        """Empty the System Event Log."""
        raise HomeAssistantError(f'{self.host} does not support clearing its event log')

    @abstractmethod
    async def set_power(self, action: str) -> None:
        """Apply POWER_ON or POWER_GRACEFUL_SHUTDOWN."""

    async def close(self) -> None:
        """Release server-side resources (sessions)."""
