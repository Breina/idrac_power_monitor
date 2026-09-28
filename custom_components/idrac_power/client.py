"""Backend-agnostic iDRAC client interface, data model and errors."""
from __future__ import annotations

import ssl
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import aiohttp
from homeassistant.exceptions import HomeAssistantError

# iDRAC answers are slow (an iDRAC6 regularly needs several seconds per page),
# but a request that has not completed within this delay is a dead one.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=45)

POWER_ON = 'On'
POWER_GRACEFUL_SHUTDOWN = 'GracefulShutdown'


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


@dataclass
class IdracData:
    """One poll's worth of data. None means "not available on this iDRAC"."""
    power_on: bool | None = None
    power_watts: float | None = None
    energy_kwh: float | None = None
    health_ok: bool | None = None
    fans: dict[str, Reading] = field(default_factory=dict)
    temperatures: dict[str, Reading] = field(default_factory=dict)
    # PSU id -> (name, healthy)
    power_supplies: dict[str, tuple[str, bool | None]] = field(default_factory=dict)


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

    @abstractmethod
    async def set_power(self, action: str) -> None:
        """Apply POWER_ON or POWER_GRACEFUL_SHUTDOWN."""

    async def close(self) -> None:
        """Release server-side resources (sessions)."""
