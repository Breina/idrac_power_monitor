"""Polling coordinator shared by every entity of one iDRAC."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import CannotConnect, IdracClient, IdracData, IdracInfo, InvalidAuth, RedfishConfig, SessionLimit
from .const import CONF_INTERVAL, CONF_INTERVAL_DEFAULT, DOMAIN

_LOGGER = logging.getLogger(__name__)


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

    async def _async_update_data(self) -> IdracData:
        try:
            return await self.client.fetch()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(f'Credentials rejected by {self.client.host}') from err
        except (CannotConnect, RedfishConfig, SessionLimit) as err:
            raise UpdateFailed(str(err)) from err
