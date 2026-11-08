"""Redfish backend (iDRAC 7/8/9) through a full Home Assistant setup."""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power import redfish
from custom_components.idrac_power.const import DOMAIN

from .common import BASE, CHASSIS, IDRAC8, serve, setup


async def test_idrac8_entities_keep_1x_unique_ids(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    # iDRAC 7/8 without the energy counter in Redfish, and /data answering 404
    serve(aioclient_mock, IDRAC8)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    prefix = 'CN123_PowerEdge R720_'
    for suffix in ('power', 'status', 'power_on', 'power_off', 'refresh',
                   'fan_0x17||Fan.Embedded.1A', 'temp_iDRAC.Embedded.1#CPU1Temp'):
        assert prefix + suffix in unique_ids
    # Absent PSU skipped, no energy source found → no energy entity
    assert prefix + 'psu_PSU.Slot.3' not in unique_ids
    assert prefix + 'energy' not in unique_ids

    def state(unique_id: str) -> str:
        entity_id = registry.async_get_entity_id(
            unique_id.split(':')[0], DOMAIN, prefix + unique_id.split(':')[1])
        return hass.states.get(entity_id).state

    assert state('sensor:power') == '112'
    assert state('binary_sensor:status') == 'off'
    assert state('switch:power_on') == 'off'
    assert state('binary_sensor:health') == 'on'  # problem
    assert state('binary_sensor:psu_PSU.Slot.1') == 'off'
    assert state('binary_sensor:psu_PSU.Slot.2') == 'on'
    assert state('sensor:fan_0x17||Fan.Embedded.1A') == '3480'
    assert state('sensor:temp_iDRAC.Embedded.1#CPU1Temp') == '41'


async def test_idrac9_subsystem_fallback_follows_absolute_odata_ids(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """Recent iDRAC 9: no /Power nor /Thermal, collections not expanded."""
    resources = {k: v for k, v in IDRAC8.items() if not k.endswith(('/Power', '/Thermal'))}
    resources[f'{BASE}/Systems/System.Embedded.1'] = {'PowerState': 'On', 'Status': {'HealthRollup': 'OK'}}
    resources[f'{CHASSIS}/EnvironmentMetrics'] = {'PowerWatts': {'Reading': 230}, 'EnergykWh': {'Reading': 1234.5}}
    resources[f'{CHASSIS}/ThermalSubsystem/Fans'] = {
        'Members': [{'@odata.id': '/redfish/v1/Chassis/System.Embedded.1/ThermalSubsystem/Fans/Fan.Embedded.1'}]}
    resources[f'{CHASSIS}/ThermalSubsystem/Fans/Fan.Embedded.1'] = {
        'Id': 'Fan.Embedded.1', 'Name': 'Fan 1', 'SpeedPercent': {'SpeedRPM': 5160, 'Reading': 30}}
    resources[f'{CHASSIS}/Sensors'] = {'Members': [
        {'@odata.id': '/redfish/v1/Chassis/System.Embedded.1/Sensors/SystemBoardInletTemp'},
        # Not a temperature by name: never fetched
        {'@odata.id': '/redfish/v1/Chassis/System.Embedded.1/Sensors/SystemBoardPwrConsumption'},
    ]}
    resources[f'{CHASSIS}/Sensors/SystemBoardInletTemp'] = {
        'Id': 'SystemBoardInletTemp', 'Name': 'Inlet Temp', 'ReadingType': 'Temperature', 'Reading': 22}
    resources[f'{CHASSIS}/PowerSubsystem/PowerSupplies'] = {'Members': [
        {'Id': 'PSU.Slot.1', 'Name': 'PSU 1', 'Status': {'Health': 'OK', 'State': 'Enabled'}}]}
    for url in (f'{CHASSIS}/Power', f'{CHASSIS}/Thermal'):
        aioclient_mock.get(url, status=404, text='')
    serve(aioclient_mock, resources)
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    states = {s.attributes.get('friendly_name'): s.state for s in hass.states.async_all()}
    assert states['PowerEdge R720 Power usage'] == '230'
    assert states['PowerEdge R720 Energy consumption'] == '1234.5'
    assert states['PowerEdge R720 Fan 1'] == '5160'
    assert states['PowerEdge R720 Inlet Temp'] == '22'
    assert states['PowerEdge R720 Server status'] == 'on'
    assert states['PowerEdge R720 PSU 1'] == 'off'
    fetched = {str(call[1]) for call in aioclient_mock.mock_calls}
    assert f'{CHASSIS}/Sensors/SystemBoardPwrConsumption' not in fetched


async def test_power_switch_posts_reset(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, IDRAC8)
    aioclient_mock.post(f'{BASE}/Systems/System.Embedded.1/Actions/ComputerSystem.Reset', status=204)
    await setup(hass)

    await hass.services.async_call('switch', 'turn_on', {'entity_id': 'switch.poweredge_r720_power'}, blocking=True)
    resets = [call for call in aioclient_mock.mock_calls if str(call[1]).endswith('ComputerSystem.Reset')]
    assert resets and resets[0][2] == {'ResetType': 'On'}

    for button, reset_type in (('force_power_off', 'ForceOff'), ('force_restart', 'ForceRestart')):
        await hass.services.async_call('button', 'press', {'entity_id': f'button.poweredge_r720_{button}'},
                                       blocking=True)
        resets = [call for call in aioclient_mock.mock_calls if str(call[1]).endswith('ComputerSystem.Reset')]
        assert resets[-1][2] == {'ResetType': reset_type}


async def test_refused_resource_after_the_system_one_is_no_reason_to_reauthenticate(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """The credentials were just accepted: a 403 further on is a missing privilege."""
    aioclient_mock.get(f'{CHASSIS}/Thermal', status=403, text='')
    serve(aioclient_mock, IDRAC8)
    entry = await setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress()
    states = {s.attributes.get('friendly_name'): s.state for s in hass.states.async_all()}
    assert states['PowerEdge R720 Power usage'] == '112'
    assert not any(name.endswith('Fan1A') for name in states)


def logins(aioclient_mock: AiohttpClientMocker) -> int:
    return sum(1 for call in aioclient_mock.mock_calls if str(call[1]).endswith(('/data/login', '/bmc/session')))


async def test_refused_energy_login_is_not_retried(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                   monkeypatch):
    """#51: every attempt is a failed login in the iDRAC's audit log."""
    monkeypatch.setattr(redfish, 'ENERGY_RETRY_AFTER_ERROR', 0)
    # iDRAC 7: the account may use Redfish, not the web GUI (authResult 1)
    aioclient_mock.get(f'{BASE}/Managers/iDRAC.Embedded.1', json={'FirmwareVersion': '2.65.65.65'})
    aioclient_mock.get('https://10.0.0.2/start.html', text='')
    aioclient_mock.post('https://10.0.0.2/data/login', text='<root><authResult>1</authResult></root>')
    serve(aioclient_mock, IDRAC8)
    entry = await setup(hass)
    assert logins(aioclient_mock) == 1

    for _ in range(3):
        await entry.runtime_data.async_refresh()
    assert logins(aioclient_mock) == 1
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client.diagnostics()['energy_unsupported'] == ['data']


async def test_refused_sysmgmt_login_is_not_retried(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                    monkeypatch):
    monkeypatch.setattr(redfish, 'ENERGY_RETRY_AFTER_ERROR', 0)
    aioclient_mock.get(f'{BASE}/Managers/iDRAC.Embedded.1', json={'FirmwareVersion': '4.40.00.00'})
    aioclient_mock.post('https://10.0.0.2/sysmgmt/2015/bmc/session', status=401, json={'authResult': 1})
    serve(aioclient_mock, IDRAC8)
    entry = await setup(hass)
    await entry.runtime_data.async_refresh()
    assert logins(aioclient_mock) == 2  # one refused sysmgmt login, plus the /data login that answered 404


async def test_failing_energy_source_is_tried_again_later(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                          monkeypatch):
    monkeypatch.setattr(redfish, 'ENERGY_RETRY_AFTER_ERROR', 0)
    aioclient_mock.get(f'{BASE}/Managers/iDRAC.Embedded.1', json={'FirmwareVersion': '2.65.65.65'})
    aioclient_mock.get('https://10.0.0.2/start.html', text='')
    aioclient_mock.post('https://10.0.0.2/data/login', status=500)
    serve(aioclient_mock, IDRAC8)
    entry = await setup(hass)
    await entry.runtime_data.async_refresh()
    assert logins(aioclient_mock) == 2


async def test_psu_redundancy_follows_the_power_resource(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    resources = dict(IDRAC8)
    power = dict(IDRAC8[f'{CHASSIS}/Power'])
    power['Redundancy'] = [{'Name': 'System Board PS Redundancy', 'Status': {'Health': 'Critical', 'State': 'Enabled'}}]
    resources[f'{CHASSIS}/Power'] = power
    serve(aioclient_mock, resources)
    await setup(hass)

    entity_id = er.async_get(hass).async_get_entity_id('binary_sensor', DOMAIN, 'CN123_PowerEdge R720_psu_redundancy')
    assert hass.states.get(entity_id).state == 'on'  # problem


async def test_no_redundancy_sensor_when_the_idrac_reports_none(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, IDRAC8)
    await setup(hass)
    assert er.async_get(hass).async_get_entity_id(
        'binary_sensor', DOMAIN, 'CN123_PowerEdge R720_psu_redundancy') is None
