"""Fake iDRAC for UI development: use MOCK as host name."""
from __future__ import annotations

from datetime import UTC, datetime

from .client import (
    POWER_FORCE_RESTART,
    POWER_ON,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    IdracClient,
    IdracData,
    IdracInfo,
    LogEntry,
    Reading,
)


class IdracMock(IdracClient):
    api = 'mock'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.power_on = True
        self.energy = 42.5
        self.events = [
            LogEntry(SEVERITY_OK, datetime(2026, 6, 18, 15, 32, 54, tzinfo=UTC), 'Log cleared.'),
            LogEntry(SEVERITY_CRITICAL, datetime(2026, 9, 28, 7, 28, 16, tzinfo=UTC), 'Fault detected on Drive 0.'),
        ]

    async def get_info(self) -> IdracInfo:
        return IdracInfo(name='Mock Device', manufacturer='Mock Manufacturer', model='Mock Model',
                         serial='Mock Serial', firmware='1.0.0')

    async def fetch(self) -> IdracData:
        self.energy += 0.1
        return IdracData(
            power_on=self.power_on,
            power_watts=100 if self.power_on else 5,
            energy_kwh=round(self.energy, 1),
            health_ok=True,
            fans={'MemberID 1': Reading('First Mock Fan', 1), 'MemberID 2': Reading('Second Mock Fan', 2)},
            temperatures={'MemberID 3': Reading('Mock Temperature', 10)},
            power_supplies={'PSU1': ('PS1 Status', True), 'PSU2': ('PS2 Status', False)},
        )

    async def fetch_events(self) -> list[LogEntry] | None:
        return list(self.events)

    async def clear_events(self) -> None:
        self.events = [LogEntry(SEVERITY_OK, datetime.now(UTC), 'Log cleared.')]

    async def set_power(self, action: str) -> None:
        # A forced restart leaves a running server running; everything else but On stops it.
        self.power_on = action == POWER_ON or (action == POWER_FORCE_RESTART and self.power_on)
