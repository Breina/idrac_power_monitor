"""Config flow for the iDrac power usage monitor"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    JSON_MODEL,
)
from .helpers import IdracRest

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str
    }
)


async def validate_input(data: dict[str, Any]) -> dict[str, Any]:
    rest_client = IdracRest(
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD]
    )

    model_name = rest_client.get_device_info()[JSON_MODEL]
    return dict(model_name=model_name)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for iDrac REST."""

    VERSION = 1

    async def async_step_user(
            self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            info = await validate_input(user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except RedfishConfig:
            errors["base"] = "redfish_config"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"

        else:
            return self.async_create_entry(title=info["model_name"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class RedfishConfig(HomeAssistantError):
    """Error to indicate that Redfish was not properly configured"""

    def __init__(self) -> None:
        super().__init__(
            'Start the iDRAC GUI with a web browser and traverse to the Network Service page - Server -> iDRAC '
            'Settings -> Network -> Services -> Redfish. Verify that Enabled is selected.'
        )
