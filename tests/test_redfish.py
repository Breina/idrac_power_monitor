"""Redfish backend (iDRAC 7/8/9) through a full Home Assistant setup."""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power.const import DOMAIN

BASE = 'https://10.0.0.2/redfish/v1'
CHASSIS = f'{BASE}/Chassis/System.Embedded.1'

IDRAC8 = {
    f'{CHASSIS}': {'Name': 'Computer System Chassis', 'Manufacturer': 'Dell Inc.',
                   'Model': 'PowerEdge R720', 'SerialNumber': 'CN123', 'Status': {'State': 'Enabled'}},
    f'{BASE}/Managers/iDRAC.Embedded.1': {'FirmwareVersion': '2.86.86.86'},
    # Chassis says "Enabled" while the host is off: status must follow PowerState
    f'{BASE}/Systems/System.Embedded.1': {'PowerState': 'Off', 'Status': {'HealthRollup': 'Warning'}},
    f'{CHASSIS}/Power': {
        'PowerControl': [{'PowerConsumedWatts': 112, 'PowerMetrics': {'AverageConsumedWatts': 110}}],
        'PowerSupplies': [
            {'MemberId': 'PSU.Slot.1', 'Name': 'PS1 Status', 'Status': {'Health': 'OK', 'State': 'Enabled'}},
            {'MemberId': 'PSU.Slot.2', 'Name': 'PS2 Status', 'Status': {'Health': 'Critical', 'State': 'Enabled'}},
            {'MemberId': 'PSU.Slot.3', 'Name': 'PS3 Status', 'Status': {'State': 'Absent'}},
        ],
    },
    f'{CHASSIS}/Thermal': {
        'Fans': [{'MemberId': '0x17||Fan.Embedded.1A', 'FanName': 'System Board Fan1A', 'Reading': 3480}],
        'Temperatures': [{'MemberId': 'iDRAC.Embedded.1#CPU1Temp', 'Name': 'CPU1 Temp', 'ReadingCelsius': 41}],
    },
}


def serve(aioclient_mock: AiohttpClientMocker, resources: dict) -> None:
    for url, body in resources.items():
        aioclient_mock.get(url, json=body)


async def setup(hass: HomeAssistant, data: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=data or {
        'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'redfish'})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_idrac8_entities_keep_1x_unique_ids(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, IDRAC8)
    # iDRAC 7/8 without the energy counter in Redfish: /data answers 404 here
    aioclient_mock.get('https://10.0.0.2/start.html', status=404)
    aioclient_mock.post('https://10.0.0.2/data/login', status=404)
    aioclient_mock.post('https://10.0.0.2/sysmgmt/2015/bmc/session', status=404)
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
    for url, body in resources.items():
        aioclient_mock.get(url, json=body)
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
    aioclient_mock.get('https://10.0.0.2/start.html', status=404)
    aioclient_mock.post('https://10.0.0.2/data/login', status=404)
    aioclient_mock.post('https://10.0.0.2/sysmgmt/2015/bmc/session', status=404)
    aioclient_mock.post(f'{BASE}/Systems/System.Embedded.1/Actions/ComputerSystem.Reset', status=204)
    await setup(hass)

    await hass.services.async_call('switch', 'turn_on', {'entity_id': 'switch.poweredge_r720_power'}, blocking=True)
    resets = [call for call in aioclient_mock.mock_calls if str(call[1]).endswith('ComputerSystem.Reset')]
    assert resets and resets[0][2] == {'ResetType': 'On'}
