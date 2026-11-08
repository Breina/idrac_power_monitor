"""Diagnostics download."""
import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power.diagnostics import async_get_config_entry_diagnostics

from .common import IDRAC8, serve, setup


async def test_diagnostics_hide_credentials_and_service_tag(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, IDRAC8)
    entry = await setup(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    text = json.dumps(diagnostics)
    for secret in ('calvin', '10.0.0.2', 'CN123'):
        assert secret not in text
    assert diagnostics['entry']['data']['interval'] == 300
    assert diagnostics['server']['model'] == 'PowerEdge R720'
    assert diagnostics['api'] == 'redfish'
    assert diagnostics['client']['thermal_source'] == 'thermal'
    assert diagnostics['data']['power_watts'] == 112
    assert diagnostics['data']['power_supplies']['PSU.Slot.2'] == {
        'name': 'PS2 Status', 'healthy': False, 'input_watts': None, 'input_volts': None, 'input_amps': None}
