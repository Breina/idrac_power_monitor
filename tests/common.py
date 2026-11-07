"""Shared by the Redfish tests: a captured iDRAC 8 and helpers to serve and set it up."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.idrac_power.const import DOMAIN

IDRAC = 'https://10.0.0.2'
BASE = f'{IDRAC}/redfish/v1'
CHASSIS = f'{BASE}/Chassis/System.Embedded.1'
MANAGER = f'{BASE}/Managers/iDRAC.Embedded.1'
CONFIG = {'host': '10.0.0.2', 'username': 'root', 'password': 'calvin', 'interval': 300, 'api': 'redfish'}

IDRAC8 = {
    f'{CHASSIS}': {'Name': 'Computer System Chassis', 'Manufacturer': 'Dell Inc.',
                   'Model': 'PowerEdge R720', 'SerialNumber': 'CN123', 'Status': {'State': 'Enabled'}},
    MANAGER: {'FirmwareVersion': '2.86.86.86'},
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

# The mocker answers with the first registration that matches: register these
# after a test's own resources, so they only answer what the test left out.


def no_web_api(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer 404 for the web GUI APIs the energy counter falls back on (/data, sysmgmt)."""
    aioclient_mock.get(f'{IDRAC}/start.html', status=404)
    aioclient_mock.post(f'{IDRAC}/data/login', status=404)
    aioclient_mock.post(f'{IDRAC}/sysmgmt/2015/bmc/session', status=404)


def no_event_log(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer 404 for the System Event Log, as an iDRAC without a readable one does."""
    for url in (f'{MANAGER}/LogServices', f'{MANAGER}/Logs/Sel'):
        aioclient_mock.get(url, status=404, text='')


def serve(aioclient_mock: AiohttpClientMocker, resources: dict) -> None:
    """Serve `resources` as JSON, with neither web API nor event log."""
    for url, body in resources.items():
        aioclient_mock.get(url, json=body)
    no_web_api(aioclient_mock)
    no_event_log(aioclient_mock)


async def setup(hass: HomeAssistant, data: dict | None = None, entry: MockConfigEntry | None = None,
                entry_id: str | None = None) -> MockConfigEntry:
    if entry is None:
        entry = MockConfigEntry(domain=DOMAIN, data=data or CONFIG, entry_id=entry_id)
        entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
