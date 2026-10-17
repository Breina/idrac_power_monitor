"""Polling coordinator shared by every entity of one iDRAC."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    Alert,
    CannotConnect,
    IdracClient,
    IdracData,
    IdracInfo,
    InvalidAuth,
    RedfishConfig,
    SessionLimit,
)
from .const import CONF_INTERVAL, CONF_INTERVAL_DEFAULT, DOMAIN

_LOGGER = logging.getLogger(__name__)

ALERT_STORE_VERSION = 1


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
        # Event log entries already reported, kept across restarts so entries
        # logged while Home Assistant was down are still reported, once.
        self._alert_store: Store[dict] = Store(hass, ALERT_STORE_VERSION, f'{DOMAIN}.{entry.entry_id}.alerts')
        self._seen_alerts: set[str] | None = None
        # Entries that appeared in the latest poll; read by the alert event entity
        self.new_alerts: list[Alert] = []

    async def _async_update_data(self) -> IdracData:
        try:
            data = await self.client.fetch()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(f'Credentials rejected by {self.client.host}') from err
        except (CannotConnect, RedfishConfig, SessionLimit) as err:
            raise UpdateFailed(str(err)) from err
        await self._track_alerts(data.alerts)
        return data

    async def _track_alerts(self, alerts: list[Alert] | None) -> None:
        self.new_alerts = []
        if alerts is None:
            return
        if self._seen_alerts is None:
            stored = await self._alert_store.async_load()
            if stored is None:
                # First read ever: what is in the log already is history, not news
                self._seen_alerts = {alert.key for alert in alerts}
                await self._alert_store.async_save({'seen': sorted(self._seen_alerts)})
                return
            self._seen_alerts = set(stored.get('seen', []))

        current = {alert.key for alert in alerts}
        self.new_alerts = [alert for alert in alerts if alert.key not in self._seen_alerts]
        if current != self._seen_alerts:
            # Only what the log still holds: cleared entries drop out
            self._seen_alerts = current
            await self._alert_store.async_save({'seen': sorted(current)})
        for alert in self.new_alerts:
            _LOGGER.info('%s logged (%s): %s', self.info.model, alert.severity, alert.message)

    async def async_remove_alert_store(self) -> None:
        await self._alert_store.async_remove()
