"""iDRAC 6 through the web GUI /data API."""
from pathlib import Path

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse
from yarl import URL

from custom_components.idrac_power.const import DOMAIN
from custom_components.idrac_power.legacy import INFO_KEYS, POLL_KEYS, escape_credential

FIXTURES = Path(__file__).parent / 'fixtures'
IDRAC = 'https://10.0.0.6'


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def idrac6(aioclient_mock: AiohttpClientMocker, login: str | None = None, poll: str | None = None) -> None:
    # iDRAC 6 has no Redfish
    aioclient_mock.get(f'{IDRAC}/redfish/v1/Chassis/System.Embedded.1', status=404, text='<html>not found</html>')
    aioclient_mock.get(f'{IDRAC}/start.html', text='<html></html>')
    aioclient_mock.post(f'{IDRAC}/data/login', text=login or fixture('idrac6_login.xml'))
    aioclient_mock.post(f'{IDRAC}/data?get={INFO_KEYS}', text=fixture('idrac6_info.xml'))
    aioclient_mock.post(f'{IDRAC}/data?get={POLL_KEYS}', text=poll or fixture('idrac6_poll.xml'))
    aioclient_mock.post(f'{IDRAC}/data?set=pwState:5', text='<root><status>ok</status></root>')
    aioclient_mock.get(f'{IDRAC}/data/logout', text='')


def calls(aioclient_mock: AiohttpClientMocker, path: str) -> list:
    return [call for call in aioclient_mock.mock_calls if call[1].path == path]


def test_credentials_are_escaped_like_the_login_page():
    assert escape_credential('calvin') == 'calvin'
    assert escape_credential('p@ss,w:rd&1+%') == 'p@040ss@02Cw@03Ard@0261@02B@025'
    assert escape_credential('a\\b') == 'a\\\\b'


async def test_flow_falls_back_to_the_web_api(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    idrac6(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {
        'host': '10.0.0.6', 'username': 'root', 'password': 'calvin', 'interval': 300})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    assert result['title'] == 'PowerEdge R910'
    assert result['data']['api'] == 'legacy'
    assert result['result'].unique_id == '17JBYX1'
    # The session used for validation is released
    assert len(calls(aioclient_mock, '/data/logout')) == 1
    login = calls(aioclient_mock, '/data/login')[0]
    assert login[2] == b'user=root&password=calvin'


async def test_session_limit_is_reported(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    idrac6(aioclient_mock, login='<root><status>ok</status><authResult>5</authResult></root>')
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {
        'host': '10.0.0.6', 'username': 'root', 'password': 'calvin', 'interval': 300})
    assert result['errors'] == {'base': 'session_limit'}


async def test_blocked_address_is_not_a_bad_password(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    idrac6(aioclient_mock, login='<root><status>ok</status><authResult>1</authResult>'
                                 '<blockingTime>60</blockingTime></root>')
    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result['flow_id'], {
        'host': '10.0.0.6', 'username': 'root', 'password': 'calvin', 'interval': 300})
    assert result['errors'] == {'base': 'cannot_connect'}


async def test_idrac6_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    idrac6(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data={
        'host': '10.0.0.6', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'legacy'})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    states = {s.attributes.get('friendly_name'): s.state for s in hass.states.async_all()}
    assert states['PowerEdge R910 Power usage'] == '168'
    assert states['PowerEdge R910 Energy consumption'] == '741.054'
    assert states['PowerEdge R910 Server status'] == 'on'
    assert states['PowerEdge R910 system board ambient'] == '19'
    assert states['PowerEdge R910 system board 1'] == '1440'
    assert states['PowerEdge R910 system board 2'] == '1560'
    assert states['PowerEdge R910 PS 1'] == 'off'
    assert states['PowerEdge R910 PS 2'] == 'on'  # input lost
    assert states['PowerEdge R910 Hardware health'] == 'on'  # problem, because of PS 2

    registry = er.async_get(hass)
    assert registry.async_get_entity_id('sensor', DOMAIN, '17JBYX1_PowerEdge R910_power')

    # The same session serves every poll
    await entry.runtime_data.async_refresh()
    assert len(calls(aioclient_mock, '/data/login')) == 1

    await hass.services.async_call('switch', 'turn_off', {'entity_id': 'switch.poweredge_r910_power'},
                                   blocking=True)
    assert [c for c in calls(aioclient_mock, '/data') if c[1].query.get('set') == 'pwState:5']

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len(calls(aioclient_mock, '/data/logout')) == 1


async def test_expired_session_logs_in_again(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    answers = iter([
        AiohttpClientMockResponse('POST', URL(IDRAC), text=fixture('idrac6_poll.xml')),
        # The iDRAC forgot the session and sends the browser to its login page
        AiohttpClientMockResponse('POST', URL(IDRAC), status=302, headers={'Location': '/login.html'}),
        AiohttpClientMockResponse('POST', URL(IDRAC), text=fixture('idrac6_poll.xml')),
    ])

    async def poll(method, url, data):
        return next(answers)

    aioclient_mock.post(f'{IDRAC}/data?get={POLL_KEYS}', side_effect=poll)
    idrac6(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data={
        'host': '10.0.0.6', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'legacy'})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, '/data/login')) == 1

    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success
    assert len(calls(aioclient_mock, '/data/login')) == 2
