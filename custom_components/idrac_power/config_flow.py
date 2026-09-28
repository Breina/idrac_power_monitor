"""Config flow for the iDRAC power usage monitor"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from .client import CannotConnect, IdracInfo, InvalidAuth, RedfishConfig, SessionLimit, normalize_host
from .const import CONF_API, CONF_INTERVAL, CONF_INTERVAL_DEFAULT, CONF_INTERVAL_MIN, DOMAIN
from .factory import async_create_client

_LOGGER = logging.getLogger(__name__)

INTERVAL = vol.All(vol.Coerce(int), vol.Range(min=CONF_INTERVAL_MIN))


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
        vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, vol.UNDEFINED)): str,
        vol.Required(CONF_PASSWORD): str,
    })


class IdracConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for iDRAC."""

    VERSION = 1

    async def _validate(self, user_input: dict[str, Any]) -> tuple[dict[str, str], IdracInfo | None, str | None]:
        """Try the credentials; return (errors, server identity, detected API)."""
        try:
            client, info = await async_create_client(
                self.hass, user_input[CONF_HOST], user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
        except InvalidAuth:
            return {'base': 'invalid_auth'}, None, None
        except SessionLimit:
            return {'base': 'session_limit'}, None, None
        except RedfishConfig:
            return {'base': 'redfish_config'}, None, None
        except CannotConnect as err:
            _LOGGER.warning('Cannot connect to %s: %s', user_input[CONF_HOST], err)
            return {'base': 'cannot_connect'}, None, None
        except Exception:
            _LOGGER.exception('Unexpected exception')
            return {'base': 'unknown'}, None, None
        await client.close()
        await client.session.close()
        return {}, info, client.api

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = normalize_host(user_input[CONF_HOST])
            errors, info, api = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(info.serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f'{info.model} ({info.serial})',
                                               data={**user_input, CONF_API: api})

        schema = _connection_schema(user_input or {}).extend({
            vol.Required(CONF_INTERVAL, default=CONF_INTERVAL_DEFAULT): INTERVAL,
        })
        return self.async_show_form(step_id='user', data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change host or credentials of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = normalize_host(user_input[CONF_HOST])
            errors, info, api = await self._validate(user_input)
            if not errors:
                if entry.unique_id:
                    await self.async_set_unique_id(info.serial)
                    self._abort_if_unique_id_mismatch(reason='wrong_server')
                return self.async_update_reload_and_abort(entry, data_updates={**user_input, CONF_API: api})

        return self.async_show_form(step_id='reconfigure', data_schema=_connection_schema(user_input or entry.data),
                                    errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**entry.data, **user_input}
            errors, _, api = await self._validate(user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={**user_input, CONF_API: api})

        schema = vol.Schema({
            vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id='reauth_confirm', data_schema=schema, errors=errors,
                                    description_placeholders={'host': entry.data[CONF_HOST]})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IdracOptionsFlow()


class IdracOptionsFlow(OptionsFlow):
    """Polling interval, changeable without re-entering credentials."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_INTERVAL, self.config_entry.data.get(CONF_INTERVAL, CONF_INTERVAL_DEFAULT))
        return self.async_show_form(step_id='init', data_schema=vol.Schema({
            vol.Required(CONF_INTERVAL, default=current): INTERVAL,
        }))
