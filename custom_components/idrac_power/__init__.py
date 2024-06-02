"""iDrac power usage monitor"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, DATA_IDRAC_REST_CLIENT, HOST, USERNAME, PASSWORD, CONF_INTERVAL, CONF_INTERVAL_DEFAULT
from .idrac_rest import IdracMock, IdracRest


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the iDrac connection from a config entry."""

    if entry.data[HOST] == 'MOCK':
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            DATA_IDRAC_REST_CLIENT: IdracMock(
                entry.data[HOST],
                entry.data[USERNAME],
                entry.data[PASSWORD],
                entry.data.get(CONF_INTERVAL, CONF_INTERVAL_DEFAULT)
            )
        }
    else:
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            DATA_IDRAC_REST_CLIENT: IdracRest(
                entry.data[HOST],
                entry.data[USERNAME],
                entry.data[PASSWORD],
                entry.data.get(CONF_INTERVAL, CONF_INTERVAL_DEFAULT)
            )
        }

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(
            entry, Platform.SENSOR
        )
    )    
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(
            entry, Platform.BINARY_SENSOR
        )
    )

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(
            entry, Platform.BUTTON
        )
    )
    return True
