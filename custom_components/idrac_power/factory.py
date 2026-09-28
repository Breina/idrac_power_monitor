"""Pick the iDRAC API a host speaks and build the matching client."""
from __future__ import annotations

import logging

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import CannotConnect, IdracClient, IdracInfo, RedfishConfig, create_ssl_context
from .const import API_LEGACY, API_MOCK, API_REDFISH, DOMAIN, MOCK_HOST
from .legacy import IdracLegacy
from .mock import IdracMock
from .redfish import IdracRedfish

_LOGGER = logging.getLogger(__name__)

CLIENTS = {API_REDFISH: IdracRedfish, API_LEGACY: IdracLegacy, API_MOCK: IdracMock}


async def _ssl_context(hass: HomeAssistant):
    store = hass.data.setdefault(DOMAIN, {})
    if 'ssl_context' not in store:
        store['ssl_context'] = await hass.async_add_executor_job(create_ssl_context)
    return store['ssl_context']


async def async_create_client(hass: HomeAssistant, host: str, username: str, password: str,
                              api: str | None = None) -> tuple[IdracClient, IdracInfo]:
    """Connect to the iDRAC and identify it.

    Without `api`, Redfish (iDRAC 7/8/9) is tried first, then the web GUI API
    that every iDRAC has, and which is the only one on iDRAC 6.
    """
    if host == MOCK_HOST:
        api = API_MOCK
    # A dedicated session per iDRAC: the legacy API keeps a session cookie,
    # and iDRACs are usually reached by IP address (hence the unsafe jar).
    session = async_create_clientsession(hass, verify_ssl=False, cookie_jar=aiohttp.CookieJar(unsafe=True))
    ssl_context = await _ssl_context(hass)

    candidates = [api] if api else [API_REDFISH, API_LEGACY]
    redfish_error: Exception | None = None
    for candidate in candidates:
        client = CLIENTS[candidate](session, ssl_context, host, username, password)
        try:
            info = await client.get_info()
        except (CannotConnect, RedfishConfig) as err:
            await client.close()
            if candidate == API_REDFISH and not api:
                _LOGGER.debug('No usable Redfish on %s (%s), trying the web API', host, err)
                redfish_error = err
                continue
            await session.close()
            if isinstance(redfish_error, RedfishConfig):
                raise redfish_error from err
            raise
        except Exception:
            await client.close()
            await session.close()
            raise
        _LOGGER.info('%s: %s %s (firmware %s) through the %s API', host, info.manufacturer, info.model,
                     info.firmware, candidate)
        return client, info

    await session.close()
    raise CannotConnect(f'No usable API on {host}')
