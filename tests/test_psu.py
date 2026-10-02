"""Per power supply power, energy, current and redundancy."""
import json
import xml.etree.ElementTree as ET
from itertools import pairwise

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power.const import DOMAIN
from custom_components.idrac_power.legacy import parse_poll
from custom_components.idrac_power.psu import EnergySplit, shares, split_power

from .test_legacy import FIXTURES, fixture, friendly_states, idrac6, setup_idrac6

# Real answers of iDRAC6 firmware 2.92, identifiers replaced
R510_ON = 'idrac6_r510_on_poll.xml'
R710_OFF = ('idrac6_r710_off_poll.xml', 'idrac6_r710_off_2_poll.xml')
# Powered-off R710 answering without any sensor: its PSU count is unknown
R710_OFF_NO_SENSORS = 'idrac6_r710_off_no_sensors_poll.xml'


def poll(name: str):
    return parse_poll(ET.fromstring(fixture(name)))


def r510(counter: float, amps: tuple[str, str] = ('0.5', '0.5')) -> str:
    """The real R510 answer with another energy counter and PSU currents."""
    text = fixture(R510_ON).replace('<ptsReadingc1>935.7</ptsReadingc1>', f'<ptsReadingc1>{counter}</ptsReadingc1>')
    for index, amp in enumerate(amps, 1):
        tag = f'ampReading{index}'
        text = text.replace(f'<{tag}>0.5</{tag}>', f'<{tag}>{amp}</{tag}>')
    return text


def test_running_r510_splits_its_power_by_psu_current():
    data = poll(R510_ON)
    assert data.power_watts == 217
    assert list(data.power_supplies) == ['PS 1', 'PS 2']
    assert [(psu.current, psu.has_current) for psu in data.power_supplies.values()] == [(0.5, True), (0.5, True)]
    # 0.5 A x 240 V would make 240 VA on a server drawing 217 W: shares of the total instead
    assert shares(data) == {'PS 1': 0.5, 'PS 2': 0.5}
    assert split_power(data, shares(data)) == {'PS 1': 108.5, 'PS 2': 108.5}
    assert not data.psu_redundancy_listed

    uneven = parse_poll(ET.fromstring(r510(935.7, ('0.7', '0.3'))))
    split = split_power(uneven, shares(uneven))
    assert split == {'PS 1': pytest.approx(151.9), 'PS 2': pytest.approx(65.1)}
    assert sum(split.values()) == pytest.approx(217)


@pytest.mark.parametrize('name', R710_OFF)
def test_powered_off_r710_splits_standby_power_equally(name: str):
    data = poll(name)
    assert data.power_on is False
    assert data.power_watts == 26  # pcAveLm
    # No current while the host is off ("powerOff"): equal split between the listed PSUs
    assert [psu.current for psu in data.power_supplies.values()] == [None, None]
    assert split_power(data, shares(data)) == {'PS 1': 13.0, 'PS 2': 13.0}
    # The redundancy sensor is not a PSU
    assert list(data.power_supplies) == ['PS 1', 'PS 2']
    assert data.psu_redundancy_listed
    assert data.psu_redundancy_ok is None


def test_answer_without_psus_publishes_no_per_psu_value():
    data = poll(R710_OFF_NO_SENSORS)
    assert data.power_watts == 26
    assert data.power_supplies == {}
    assert shares(data) == {}
    assert split_power(data, shares(data)) == {}


def test_no_split_without_per_psu_readings():
    """iDRAC 7/8 layout of /data, without ampReading: no per-PSU values at all."""
    data = poll('idrac6_poll_psu_lost.xml')
    assert list(data.power_supplies) == ['PS 1', 'PS 2']
    assert shares(data) == {}


def test_redundancy_state():
    text = fixture(R710_OFF[0])
    lost = text.replace('<sensor><sensorStatus>Unknown</sensorStatus>\n<location>system board PS redundancydancy',
                        '<sensor><sensorStatus>Critical</sensorStatus>\n<location>system board PS redundancydancy')
    assert lost != text
    assert parse_poll(ET.fromstring(lost)).psu_redundancy_ok is False
    ok = lost.replace('<sensorStatus>Critical</sensorStatus>', '<sensorStatus>Normal</sensorStatus>')
    assert parse_poll(ET.fromstring(ok)).psu_redundancy_ok is True


def test_energy_split_adds_up_to_the_counter():
    half = {'PS 1': 0.5, 'PS 2': 0.5}
    split = EnergySplit()
    counted = 0.0

    def step(counter, psu_shares, increase=0.0):
        nonlocal counted
        split.update(counter, psu_shares)
        counted += increase
        assert sum(split.energy.values()) == pytest.approx(counted)

    step(935.7, half)  # first reading: a baseline, not 935.7 kWh at once
    assert split.energy == {}
    step(935.9, {'PS 1': 0.7, 'PS 2': 0.3}, 0.2)  # the shares in force since the last poll
    assert split.energy == {'PS 1': pytest.approx(0.1), 'PS 2': pytest.approx(0.1)}
    step(936.9, half, 1.0)
    assert split.energy == {'PS 1': pytest.approx(0.8), 'PS 2': pytest.approx(0.4)}
    step(0.1, half)  # counter reset from the iDRAC UI: a new baseline
    step(None, half)  # no counter in this answer
    step(0.3, {}, 0.2)  # PSU count unknown now: the shares of the last poll still apply
    step(0.5, {})  # nothing known over this interval: held...
    step(0.6, half, 0.3)  # ...and split once the shares are known again
    assert split.energy == {'PS 1': pytest.approx(1.05), 'PS 2': pytest.approx(0.65)}

    # A restart carries on from the saved state, including what was used while it was down
    split = EnergySplit(json.loads(json.dumps(split.as_dict())))
    step(1.0, {'PS 1': 0.6, 'PS 2': 0.4}, 0.4)
    assert split.energy == {'PS 1': pytest.approx(1.25), 'PS 2': pytest.approx(0.85)}


async def test_r510_psu_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    idrac6(aioclient_mock, info='idrac6_r510_on_info.xml', poll=R510_ON)
    await setup_idrac6(hass)
    states = friendly_states(hass)
    assert states['PowerEdge R510 Power usage'] == '217'
    assert states['PowerEdge R510 PS 1 power usage'] == '108.5'
    assert states['PowerEdge R510 PS 2 power usage'] == '108.5'
    assert states['PowerEdge R510 PS 1 input current'] == '0.5'
    # The first counter reading is a baseline
    assert states['PowerEdge R510 PS 1 energy consumption'] == '0.0'
    assert states['PowerEdge R510 PS 1'] == 'off'  # health, unchanged
    assert 'PowerEdge R510 PSU redundancy' not in states  # this R510 lists no redundancy sensor

    registry = er.async_get(hass)
    prefix = 'SVCTAG1_PowerEdge R510_'
    for platform, suffix, entity_id in (
            ('sensor', 'psu_PS 1_power', 'sensor.poweredge_r510_ps_1_power_usage'),
            ('sensor', 'psu_PS 2_energy', 'sensor.poweredge_r510_ps_2_energy_consumption'),
            ('sensor', 'psu_PS 1_current', 'sensor.poweredge_r510_ps_1_input_current'),
            ('binary_sensor', 'psu_PS 1', 'binary_sensor.poweredge_r510_ps_1'),
            ('sensor', 'power', 'sensor.poweredge_r510_power_usage'),
            ('sensor', 'energy', 'sensor.poweredge_r510_energy_consumption')):
        assert registry.async_get_entity_id(platform, DOMAIN, prefix + suffix) == entity_id
    current = registry.async_get('sensor.poweredge_r510_ps_1_input_current')
    assert current.entity_category is EntityCategory.DIAGNOSTIC
    energy = hass.states.get('sensor.poweredge_r510_ps_1_energy_consumption')
    assert energy.attributes['state_class'] == 'total_increasing'
    assert energy.attributes['device_class'] == 'energy'
    assert energy.attributes['unit_of_measurement'] == 'kWh'


@pytest.mark.parametrize('name', R710_OFF)
async def test_r710_off_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, name: str):
    idrac6(aioclient_mock, info=name.replace('_poll', '_info'), poll=name)
    entry = await setup_idrac6(hass)
    states = friendly_states(hass)
    assert states['PowerEdge R710 PS 1 power usage'] == '13.0'
    assert states['PowerEdge R710 PS 2 power usage'] == '13.0'
    assert states['PowerEdge R710 PS 1 input current'] == 'unknown'
    assert states['PowerEdge R710 PSU redundancy'] == 'unknown'
    assert not [entity for entity in states if 'redundancydancy' in entity]

    registry = er.async_get(hass)
    unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    serial = 'SVCTAG3' if name == R710_OFF[0] else 'SVCTAG4'
    assert f'{serial}_PowerEdge R710_psu_redundancy' in unique_ids
    # What 2.1 created for the redundancy sensor, as if it were a PSU
    assert f'{serial}_PowerEdge R710_psu_system board PS redundancydancy' not in unique_ids


async def test_psus_appear_when_the_iDRAC_lists_them(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    idrac6(aioclient_mock, info='idrac6_r710_off_no_sensors_info.xml', poll=R710_OFF_NO_SENSORS)
    entry = await setup_idrac6(hass)
    states = friendly_states(hass)
    assert states['PowerEdge R710 Power usage'] == '26'
    assert not [entity for entity in states if 'PS ' in entity or 'PSU' in entity]

    aioclient_mock.clear_requests()
    idrac6(aioclient_mock, info='idrac6_r710_off_no_sensors_info.xml', poll=R710_OFF[0])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert friendly_states(hass)['PowerEdge R710 PS 2 power usage'] == '13.0'

    # Back to an answer without PSUs: no per-PSU value rather than a guess
    aioclient_mock.clear_requests()
    idrac6(aioclient_mock, info='idrac6_r710_off_no_sensors_info.xml', poll=R710_OFF_NO_SENSORS)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    states = friendly_states(hass)
    assert states['PowerEdge R710 PS 2 power usage'] == 'unavailable'
    assert states['PowerEdge R710 PS 2 energy consumption'] == 'unavailable'


async def test_psu_energy_across_polls_reset_and_restart(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict):
    entry: MockConfigEntry | None = None
    published: list[tuple[float, float]] = []

    async def answer(counter: float, amps: tuple[str, str] = ('0.5', '0.5')) -> tuple[float, float]:
        nonlocal entry
        aioclient_mock.clear_requests()
        idrac6(aioclient_mock, info='idrac6_r510_on_info.xml', poll_text=r510(counter, amps))
        if entry is None:
            entry = await setup_idrac6(hass)
        elif entry.state is ConfigEntryState.NOT_LOADED:
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        else:
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
        values = tuple(float(hass.states.get(f'sensor.poweredge_r510_ps_{i}_energy_consumption').state)
                       for i in (1, 2))
        published.append(values)
        return values

    assert await answer(935.7) == (0.0, 0.0)
    assert await answer(935.9, ('0.7', '0.3')) == (0.1, 0.1)
    assert await answer(936.9) == (0.8, 0.4)
    assert await answer(0.1) == (0.8, 0.4)  # counter reset
    assert await answer(0.5) == (1.0, 0.6)

    # Restart: the totals and the counter reading they stop at are restored
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[f'{DOMAIN}.{entry.entry_id}.psu_energy']['data']
    assert stored['counter'] == 0.5
    assert stored['energy'] == {'PS 1': pytest.approx(1.0), 'PS 2': pytest.approx(0.6)}
    # Consumed while Home Assistant was down: split with the shares in force then
    assert await answer(0.9) == (1.2, 0.8)

    # Never down, and as much as the server's counter counted
    for before, after in pairwise(published):
        assert after[0] >= before[0] and after[1] >= before[1]
    assert sum(published[-1]) == pytest.approx(0.2 + 1.0 + 0.4 + 0.4)

    # A deleted server leaves nothing behind
    assert await hass.config_entries.async_remove(entry.entry_id)
    assert f'{DOMAIN}.{entry.entry_id}.psu_energy' not in hass_storage


def idrac9(power: dict) -> dict:
    base = 'https://10.0.0.2/redfish/v1'
    chassis = f'{base}/Chassis/System.Embedded.1'
    return {
        chassis: {'Name': 'Computer System Chassis', 'Manufacturer': 'Dell Inc.', 'Model': 'PowerEdge R740',
                  'SerialNumber': 'SVCTAG9'},
        f'{base}/Managers/iDRAC.Embedded.1': {'FirmwareVersion': '6.10.30.00'},
        f'{base}/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries': {'Members': []},
        f'{base}/Systems/System.Embedded.1': {'PowerState': 'On', 'Status': {'HealthRollup': 'OK'}},
        f'{chassis}/Power': power,
        f'{chassis}/Thermal': {
            'Fans': [{'MemberId': '0x17||Fan.Embedded.1A', 'FanName': 'System Board Fan1A', 'Reading': 5160}],
            'Temperatures': [{'MemberId': 'iDRAC.Embedded.1#InletTemp', 'Name': 'System Board Inlet Temp',
                              'ReadingCelsius': 22}],
        },
    }


def sysmgmt(aioclient_mock: AiohttpClientMocker, energy: float) -> None:
    """iDRAC 9 Redfish has no energy counter: the integration reads it from the sysmgmt web API."""
    aioclient_mock.get('https://10.0.0.2/start.html', status=404)
    aioclient_mock.post('https://10.0.0.2/data/login', status=404)
    aioclient_mock.post('https://10.0.0.2/sysmgmt/2015/bmc/session', json={'authResult': 0},
                        headers={'XSRF-TOKEN': 'token'})
    aioclient_mock.get('https://10.0.0.2/sysmgmt/2015/server/sensor/power',
                       json={'root': {'powermonitordata': {'cumReading': {'totalUsage': str(energy)}}}})
    aioclient_mock.delete('https://10.0.0.2/sysmgmt/2015/bmc/session', text='')


async def test_redfish_psu_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """Power resource of an iDRAC 9 as Dell documents it (not captured on hardware)."""
    power = json.loads((FIXTURES / 'idrac9_power.json').read_text())
    for url, body in idrac9(power).items():
        aioclient_mock.get(url, json=body)
    sysmgmt(aioclient_mock, 1234.5)
    entry = MockConfigEntry(domain=DOMAIN, data={
        'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'redfish'})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = friendly_states(hass)
    assert states['PowerEdge R740 Power usage'] == '217'
    # PowerInputWatts 128 and 96: shares of the 217 W the server draws
    assert states['PowerEdge R740 PS1 power usage'] == '124.0'
    assert states['PowerEdge R740 PS2 power usage'] == '93.0'
    assert states['PowerEdge R740 PS1 energy consumption'] == '0.0'
    assert states['PowerEdge R740 PSU redundancy'] == 'off'
    assert states['PowerEdge R740 PS1 Status'] == 'off'
    # No per-PSU current in the Redfish Power schema
    assert not [entity for entity in states if 'current' in entity]

    aioclient_mock.clear_requests()
    for url, body in idrac9(power).items():
        aioclient_mock.get(url, json=body)
    sysmgmt(aioclient_mock, 1235.62)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    states = friendly_states(hass)
    assert float(states['PowerEdge R740 PS1 energy consumption']) == pytest.approx(1.12 * 128 / 224, abs=1e-4)
    assert float(states['PowerEdge R740 PS2 energy consumption']) == pytest.approx(1.12 * 96 / 224, abs=1e-4)

    registry = er.async_get(hass)
    unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    for suffix in ('psu_PSU.Slot.1_power', 'psu_PSU.Slot.2_energy', 'psu_redundancy', 'psu_PSU.Slot.1'):
        assert f'SVCTAG9_PowerEdge R740_{suffix}' in unique_ids


async def test_redfish_split_falls_back_to_equal_shares(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """A PSU without input reading: neither proportional to a mix of readings, nor a guess."""
    power = json.loads((FIXTURES / 'idrac9_power.json').read_text())
    power['PowerSupplies'][1]['PowerInputWatts'] = None
    power['PowerSupplies'][1]['LastPowerOutputWatts'] = None
    for url, body in idrac9(power).items():
        aioclient_mock.get(url, json=body)
    sysmgmt(aioclient_mock, 1234.5)
    entry = MockConfigEntry(domain=DOMAIN, data={
        'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'redfish'})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    states = friendly_states(hass)
    assert states['PowerEdge R740 PS1 power usage'] == '108.5'
    assert states['PowerEdge R740 PS2 power usage'] == '108.5'
