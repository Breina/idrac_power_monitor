"""Polling coordinator shared by every entity of one iDRAC."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import (
    CannotConnect, IdracClient, IdracData, IdracInfo, InvalidAuth, LogEntry, RedfishConfig, SessionLimit,
)
from .const import CONF_INTERVAL, CONF_INTERVAL_DEFAULT, DOMAIN

_LOGGER = logging.getLogger(__name__)

# The event log is read at most this often, however short the polling interval
EVENT_LOG_REFRESH = timedelta(seconds=60)
# Fired once per new event log record, for automations and notifications
EVENT_NAME = f'{DOMAIN}_event'


def new_entries(old: list[LogEntry], new: list[LogEntry]) -> Iterator[LogEntry]:
    """Records of `new` that `old` did not have (records have no id: compared as a multiset)."""
    seen = Counter(old)
    for entry in new:
        if seen[entry]:
            seen[entry] -= 1
        else:
            yield entry


class IdracCoordinator(DataUpdateCoordinator[IdracData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: IdracClient, info: IdracInfo):
        interval = entry.options.get(CONF_INTERVAL, entry.data.get(CONF_INTERVAL, CONF_INTERVAL_DEFAULT))
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f'{DOMAIN} {client.host}',
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.info = info
        self._events: list[LogEntry] | None = None
        self._events_fetched: datetime | None = None
        self._events_supported = True

    async def _async_update_data(self) -> IdracData:
        try:
            data = await self.client.fetch()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(f'Credentials rejected by {self.client.host}') from err
        except (CannotConnect, RedfishConfig, SessionLimit) as err:
            raise UpdateFailed(str(err)) from err
        await self._update_events()
        data.events = self._events
        return data

    async def _update_events(self, force: bool = False) -> None:
        """Refresh the event log; a failure keeps the last one rather than failing the poll."""
        if not self._events_supported:
            return
        now = dt_util.utcnow()
        if not force and self._events_fetched and now - self._events_fetched < EVENT_LOG_REFRESH:
            return
        try:
            events = await self.client.fetch_events()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(f'Credentials rejected by {self.client.host}') from err
        except (CannotConnect, RedfishConfig, SessionLimit) as err:
            _LOGGER.debug('Event log of %s unavailable: %s', self.client.host, err)
            return
        self._events_fetched = now
        if events is None:
            self._events_supported = False
            return
        if self._events is not None:  # the first read is a baseline, not news
            for entry in new_entries(self._events, events):
                self.hass.bus.async_fire(EVENT_NAME, {
                    'host': self.client.host,
                    'name': self.config_entry.title,
                    'serial': self.info.serial,
                    'severity': entry.severity,
                    'time': entry.created.isoformat() if entry.created else None,
                    'message': entry.message,
                })
        self._events = events

    async def async_clear_events(self) -> None:
        await self.client.clear_events()
        await self._update_events(force=True)
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, events=self._events))
