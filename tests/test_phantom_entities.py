"""Issue #48: phantom "PSU N" / "None" entities on an iDRAC 8 (R730xd).

Every hour or two, that iDRAC 8 puts entries whose every field is null in
front of the real PowerSupplies, Fans or Temperatures (2 + 2 PSUs, 16 + 6
fans, 7 + 4 temperatures). 1.8.0 registered them as entities keyed on their
list position and on the string "None"; no later poll provides them, so they
stayed unavailable forever.
"""
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power.const import DOMAIN

from .test_redfish import BASE, CHASSIS, setup

PREFIX = 'CN123_PowerEdge R730xd_'
CONFIG = {'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'redfish'}


def nulls(item: dict, count: int) -> list[dict]:
    """`count` entries shaped like `item` with every field null, as the iDRAC sends them."""
    return [dict.fromkeys(item) for _ in range(count)]


def r730xd(degraded: bool) -> dict:
    """The real R730xd answers (trimmed); `degraded` prepends all-null entries, as seen in the field."""
    psus = [{'MemberId': f'PSU.Slot.{n}', 'Name': f'PS{n} Status',
             'Status': {'Health': 'OK', 'State': 'Enabled'}} for n in (1, 2)]
    fans = [{'MemberId': f'0x17||Fan.Embedded.{n}', 'FanName': f'System Board Fan{n} RPM', 'Reading': 3600}
            for n in range(1, 7)]
    temps = [{'MemberId': 'iDRAC.Embedded.1#CPU1Temp', 'Name': 'CPU1 Temp', 'ReadingCelsius': 45}]
    if degraded:
        psus = nulls(psus[0], 2) + psus
        fans = nulls(fans[0], 16) + fans
        temps = nulls(temps[0], 7) + temps
    return {
        CHASSIS: {'Name': 'Computer System Chassis', 'Manufacturer': 'Dell Inc.',
                  'Model': 'PowerEdge R730xd', 'SerialNumber': 'CN123'},
        f'{BASE}/Managers/iDRAC.Embedded.1': {'FirmwareVersion': '2.86.86.86'},
        f'{BASE}/Systems/System.Embedded.1': {'PowerState': 'On', 'Status': {'HealthRollup': 'OK'}},
        f'{CHASSIS}/Power': {'PowerControl': [{'PowerConsumedWatts': 158}], 'PowerSupplies': psus},
        f'{CHASSIS}/Thermal': {'Fans': fans, 'Temperatures': temps},
    }


def serve(aioclient_mock: AiohttpClientMocker, degraded: bool = False) -> None:
    aioclient_mock.clear_requests()
    for url, body in r730xd(degraded).items():
        aioclient_mock.get(url, json=body)
    aioclient_mock.get('https://10.0.0.2/start.html', status=404)
    aioclient_mock.post('https://10.0.0.2/data/login', status=404)
    aioclient_mock.post('https://10.0.0.2/sysmgmt/2015/bmc/session', status=404)


def unique_ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    registry = er.async_get(hass)
    return {e.unique_id.removeprefix(PREFIX) for e in er.async_entries_for_config_entry(registry, entry.entry_id)}


async def test_null_entries_add_no_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog):
    caplog.set_level(logging.DEBUG, logger='custom_components.idrac_power')
    serve(aioclient_mock)
    entry = await setup(hass)
    healthy = unique_ids(hass, entry)
    assert {'psu_PSU.Slot.1', 'psu_PSU.Slot.2', 'fan_0x17||Fan.Embedded.1',
            'temp_iDRAC.Embedded.1#CPU1Temp'} <= healthy
    assert 'without identifiers' not in caplog.text

    serve(aioclient_mock, degraded=True)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert unique_ids(hass, entry) == healthy
    # The real entries behind the null ones are still read
    data = entry.runtime_data.data
    assert data.power_supplies['PSU.Slot.1'] == ('PS1 Status', True)
    assert data.fans['0x17||Fan.Embedded.1'].value == 3600
    assert data.temperatures['iDRAC.Embedded.1#CPU1Temp'].value == 45
    # The raw answer is logged at debug level, for the next iDRAC oddity
    skipped = [r.getMessage() for r in caplog.records if 'without identifiers' in r.getMessage()]
    assert len(skipped) == 2
    assert 'skipped: 2 power supplies. Response: {"PowerControl"' in skipped[0]
    assert 'skipped: 16 fans, 7 temperatures. Response: {"Fans"' in skipped[1]


async def test_phantoms_from_1_8_0_are_removed(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    entry = MockConfigEntry(domain=DOMAIN, data=CONFIG)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    phantoms = [('binary_sensor', f'psu_{n}') for n in range(9)] + [('sensor', 'fan_None'), ('sensor', 'temp_None')]
    kept = [('binary_sensor', 'psu_PSU.Slot.1'), ('sensor', 'fan_0x17||Fan.Embedded.1')]
    for domain, suffix in phantoms + kept:
        registry.async_get_or_create(domain, DOMAIN, PREFIX + suffix, config_entry=entry)

    serve(aioclient_mock)
    await setup(hass, entry=entry)

    remaining = unique_ids(hass, entry)
    assert not {suffix for _, suffix in phantoms} & remaining
    assert {suffix for _, suffix in kept} <= remaining
