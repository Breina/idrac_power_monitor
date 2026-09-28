"""Config, reconfigure and options flows."""
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power.const import DOMAIN

BASE = 'https://10.0.0.2/redfish/v1'


def redfish(aioclient_mock: AiohttpClientMocker, serial: str = 'CN123') -> None:
    aioclient_mock.get(f'{BASE}/Chassis/System.Embedded.1', json={
        'Name': 'Chassis', 'Manufacturer': 'Dell Inc.', 'Model': 'PowerEdge R720', 'SerialNumber': serial})
    aioclient_mock.get(f'{BASE}/Managers/iDRAC.Embedded.1', json={'FirmwareVersion': '2.86.86.86'})


async def test_user_flow_accepts_pasted_url(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    redfish(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {
        'host': ' https://10.0.0.2/restgui/start.html ', 'username': 'root', 'password': 'calvin', 'interval': 60})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    assert result['title'] == 'PowerEdge R720 (CN123)'
    assert result['data'] == {'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 60,
                              'api': 'redfish'}
    assert result['result'].unique_id == 'CN123'


async def test_user_flow_rejects_duplicates(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    redfish(aioclient_mock)
    MockConfigEntry(domain=DOMAIN, unique_id='CN123', data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {
        'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 60})
    assert result['type'] is FlowResultType.ABORT
    assert result['reason'] == 'already_configured'


async def test_user_flow_bad_credentials(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f'{BASE}/Chassis/System.Embedded.1', status=401)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {
        'host': '10.0.0.2', 'username': 'root', 'password': 'nope', 'interval': 60})
    assert result['type'] is FlowResultType.FORM
    assert result['errors'] == {'base': 'invalid_auth'}


async def test_reconfigure_changes_credentials(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """Issue #37: connection settings could not be edited after setup."""
    redfish(aioclient_mock)
    # A 1.x entry: no unique id, no api
    entry = MockConfigEntry(domain=DOMAIN, data={
        'host': '10.0.0.9', 'username': 'root', 'password': 'old', 'interval': 300})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result['type'] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {
        'host': '10.0.0.2', 'username': 'admin', 'password': 'new'})
    assert result['type'] is FlowResultType.ABORT
    assert result['reason'] == 'reconfigure_successful'
    assert entry.data == {'host': '10.0.0.2', 'username': 'admin', 'password': 'new', 'interval': 300,
                          'api': 'redfish'}


async def test_options_change_interval(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={
        'host': 'MOCK', 'username': 'u', 'password': 'p', 'interval': 300})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data['api'] == 'mock'

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result['flow_id'], {'interval': 30})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval.total_seconds() == 30
    assert hass.states.get('sensor.mock_model_energy_consumption').state == '42.6'
