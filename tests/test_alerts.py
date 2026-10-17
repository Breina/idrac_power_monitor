"""System Event Log alerts and per-PSU input power."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power import redfish
from custom_components.idrac_power.const import DOMAIN

from .conftest import no_event_log
from .test_redfish import BASE, CHASSIS, IDRAC8

MANAGER = f'{BASE}/Managers/iDRAC.Embedded.1'
PREFIX = 'CN123_PowerEdge R720_'


def sel_entry(entry_id: str, created: str, severity: str, message: str) -> dict:
    return {'@odata.id': f'/redfish/v1/Managers/iDRAC.Embedded.1/Logs/Sel/{entry_id}', 'Id': entry_id,
            'Created': created, 'Severity': severity, 'Message': message, 'EntryType': 'SEL',
            'MessageId': 'PSU0003'}


OLD_ENTRIES = [
    sel_entry('1', '2026-10-01T10:00:00-05:00', 'OK', 'Log cleared.'),
    sel_entry('2', '2026-10-02T11:00:00-05:00', 'Warning', 'The system board battery is low.'),
]
NEW_ENTRY = sel_entry('3', '2026-10-17T09:00:00-05:00', 'Critical', 'Power supply 2 is not receiving input power.')


def idrac8(with_psu_watts: bool = True) -> dict:
    resources = dict(IDRAC8)
    power = dict(IDRAC8[f'{CHASSIS}/Power'])
    supplies = []
    for supply, watts in zip(power['PowerSupplies'], (120, 4, None)):
        supply = dict(supply)
        if with_psu_watts and watts is not None:
            supply['PowerInputWatts'] = watts
        supplies.append(supply)
    power['PowerSupplies'] = supplies
    resources[f'{CHASSIS}/Power'] = power
    return resources


def serve(aioclient_mock: AiohttpClientMocker, resources: dict, sel: list[dict] | None) -> None:
    aioclient_mock.clear_requests()
    for url, body in resources.items():
        aioclient_mock.get(url, json=body)
    aioclient_mock.get('https://10.0.0.2/start.html', status=404)
    aioclient_mock.post('https://10.0.0.2/data/login', status=404)
    aioclient_mock.post('https://10.0.0.2/sysmgmt/2015/bmc/session', status=404)
    if sel is not None:
        # iDRAC 7/8 firmware without the Sel log service: only /Logs/Sel
        aioclient_mock.get(f'{MANAGER}/LogServices', json={'Members': [
            {'@odata.id': '/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog'}]})
        aioclient_mock.get(f'{MANAGER}/Logs/Sel', json={'Members': sel})
    no_event_log(aioclient_mock)


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, entry_id='idrac1', data={
        'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'redfish'})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass: HomeAssistant, domain: str, suffix: str) -> str:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, PREFIX + suffix)


@pytest.fixture(autouse=True)
def read_log_every_poll(monkeypatch):
    monkeypatch.setattr(redfish, 'ALERTS_MIN_INTERVAL', 0)


async def test_existing_log_is_history_and_new_entries_fire(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, idrac8(), OLD_ENTRIES)
    entry = await setup_entry(hass)

    alert = entity_id(hass, 'event', 'alert')
    assert alert is not None
    # What the log held when the server was added is not reported
    assert hass.states.get(alert).state == 'unknown'
    health = hass.states.get(entity_id(hass, 'binary_sensor', 'health'))
    assert health.state == 'on'
    assert health.attributes['latest_problem'] == 'The system board battery is low.'

    serve(aioclient_mock, idrac8(), [*OLD_ENTRIES, NEW_ENTRY])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(alert)
    assert state.state != 'unknown'
    assert state.attributes['event_type'] == 'critical'
    assert state.attributes['message'] == 'Power supply 2 is not receiving input power.'
    assert state.attributes['log_id'] == '3'
    assert state.attributes['message_id'] == 'PSU0003'
    fired_at = state.state

    # The same entry on the next poll is not reported again
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(alert).state == fired_at


async def test_entries_logged_while_home_assistant_was_down(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                             hass_storage):
    old_keys = sorted(f"{e['Id']}|{e['Created']}" for e in OLD_ENTRIES)
    hass_storage[f'{DOMAIN}.idrac1.alerts'] = {'version': 1, 'minor_version': 1,
                                               'key': f'{DOMAIN}.idrac1.alerts', 'data': {'seen': old_keys}}
    serve(aioclient_mock, idrac8(), [*OLD_ENTRIES, NEW_ENTRY])
    await setup_entry(hass)

    state = hass.states.get(entity_id(hass, 'event', 'alert'))
    assert state.attributes['event_type'] == 'critical'
    assert state.attributes['log_id'] == '3'


async def test_cleared_log_reports_entries_that_reuse_ids(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, idrac8(), OLD_ENTRIES)
    entry = await setup_entry(hass)

    # Log cleared on the iDRAC: numbering restarts at 1
    reused = sel_entry('1', '2026-10-17T12:00:00-05:00', 'Warning', 'Fan 3 RPM is less than the lower warning threshold.')
    serve(aioclient_mock, idrac8(), [reused])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id(hass, 'event', 'alert'))
    assert state.attributes['event_type'] == 'warning'
    assert state.attributes['message'].startswith('Fan 3')


async def test_psu_input_power_sensors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, idrac8(), OLD_ENTRIES)
    await setup_entry(hass)

    ps1 = hass.states.get(entity_id(hass, 'sensor', 'psu_power_PSU.Slot.1'))
    ps2 = hass.states.get(entity_id(hass, 'sensor', 'psu_power_PSU.Slot.2'))
    assert ps1.state == '120'
    assert ps1.attributes['friendly_name'] == 'PowerEdge R720 PS1 input power'
    assert ps1.attributes['unit_of_measurement'] == 'W'
    assert ps2.state == '4'
    # Absent PSU: no sensor
    assert entity_id(hass, 'sensor', 'psu_power_PSU.Slot.3') is None


async def test_no_psu_power_nor_log_adds_nothing(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    serve(aioclient_mock, idrac8(with_psu_watts=False), None)
    await setup_entry(hass)

    assert entity_id(hass, 'sensor', 'psu_power_PSU.Slot.1') is None
    assert entity_id(hass, 'event', 'alert') is None
    assert 'latest_problem' not in hass.states.get(entity_id(hass, 'binary_sensor', 'health')).attributes


async def test_idrac9_log_service_and_psu_metrics(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    resources = {k: v for k, v in IDRAC8.items() if not k.endswith(('/Power', '/Thermal'))}
    resources[f'{CHASSIS}/EnvironmentMetrics'] = {'PowerWatts': {'Reading': 230}}
    resources[f'{CHASSIS}/PowerSubsystem/PowerSupplies'] = {'Members': [
        {'Id': 'PSU.Slot.1', 'Name': 'PS1 Status', 'Status': {'Health': 'OK', 'State': 'Enabled'},
         'Metrics': {'@odata.id': '/redfish/v1/Chassis/System.Embedded.1/PowerSubsystem/PowerSupplies/PSU.Slot.1/Metrics'}}]}
    resources[f'{CHASSIS}/PowerSubsystem/PowerSupplies/PSU.Slot.1/Metrics'] = {'InputPowerWatts': {'Reading': 236}}
    resources[f'{MANAGER}/LogServices'] = {'Members': [
        {'@odata.id': '/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel'}]}
    resources[f'{MANAGER}/LogServices/Sel'] = {
        'Id': 'Sel', 'Entries': {'@odata.id': '/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries'}}
    resources[f'{MANAGER}/LogServices/Sel/Entries'] = {'Members': OLD_ENTRIES}
    aioclient_mock.clear_requests()
    for url in (f'{CHASSIS}/Power', f'{CHASSIS}/Thermal', f'{CHASSIS}/ThermalSubsystem/Fans', f'{CHASSIS}/Sensors'):
        aioclient_mock.get(url, status=404, text='')
    for url, body in resources.items():
        aioclient_mock.get(url, json=body)
    aioclient_mock.get('https://10.0.0.2/start.html', status=404)
    aioclient_mock.post('https://10.0.0.2/data/login', status=404)
    aioclient_mock.post('https://10.0.0.2/sysmgmt/2015/bmc/session', status=404)
    await setup_entry(hass)

    assert hass.states.get(entity_id(hass, 'sensor', 'psu_power_PSU.Slot.1')).state == '236'
    assert entity_id(hass, 'event', 'alert') is not None
    fetched = {str(call[1]) for call in aioclient_mock.mock_calls}
    assert f'{MANAGER}/Logs/Sel' not in fetched


# What a PowerEdge R620 (iDRAC 7, firmware 2.65.65.65) answers
R620_SUPPLIES = [
    {'MemberId': f'PSU.Slot.{slot}', 'Name': f'PS{slot} Status', 'PowerInputWatts': 900, 'LastPowerOutputWatts': None,
     'LineInputVoltage': 232, 'InputRanges': [{'InputType': 'AC', 'OutputWattage': 750}],
     'Status': {'Health': 'OK', 'State': 'Enabled'}}
    for slot in (1, 2)
]


async def test_idrac7_rated_psu_input_sel_link_and_refused_energy_login(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    resources = dict(IDRAC8)
    resources[f'{BASE}/Managers/iDRAC.Embedded.1'] = {'FirmwareVersion': '2.65.65.65'}
    resources[f'{CHASSIS}/Power'] = {
        'PowerControl': [{'PowerConsumedWatts': 164, 'PowerCapacityWatts': 896}], 'PowerSupplies': R620_SUPPLIES}
    # The Sel service links to /Logs/Sel; LogServices/Sel/Entries answers with the service itself
    resources[f'{MANAGER}/LogServices'] = {'Members': [
        {'@odata.id': '/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel'},
        {'@odata.id': '/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog'}]}
    resources[f'{MANAGER}/LogServices/Sel'] = {
        'Id': 'Sel', 'Entries': {'@odata.id': '/redfish/v1/Managers/iDRAC.Embedded.1/Logs/Sel'}}
    resources[f'{MANAGER}/LogServices/Sel/Entries'] = resources[f'{MANAGER}/LogServices/Sel']
    resources[f'{MANAGER}/Logs/Sel'] = {'Members': OLD_ENTRIES}
    aioclient_mock.clear_requests()
    for url, body in resources.items():
        aioclient_mock.get(url, json=body)
    aioclient_mock.get('https://10.0.0.2/start.html', text='')
    # The web GUI login refuses these credentials (authResult 1)
    aioclient_mock.post('https://10.0.0.2/data/login', text='<root><authResult>1</authResult></root>')
    entry = await setup_entry(hass)

    # Rated values, not readings: no per-PSU sensors
    assert entity_id(hass, 'sensor', 'psu_power_PSU.Slot.1') is None
    health = hass.states.get(entity_id(hass, 'binary_sensor', 'health'))
    assert health.attributes['latest_problem'] == 'The system board battery is low.'

    def logins() -> int:
        return sum(1 for call in aioclient_mock.mock_calls if str(call[1]).endswith(('/data/login', '/bmc/session')))

    # One refused /data login, no sysmgmt (iDRAC 9 only) ...
    assert logins() == 1
    # ... and no new attempt on the next polls
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.async_refresh()
    assert logins() == 1
