"""Fake iDRAC for UI development: use MOCK as host name."""
from __future__ import annotations

from .client import (
    POWER_FORCE_RESTART,
    POWER_ON,
    Alert,
    IdracClient,
    IdracData,
    IdracInfo,
    PowerSupply,
    Reading,
)


class IdracMock(IdracClient):
    api = 'mock'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.power_on = True
        self.energy = 42.5

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
            power_supplies={
                'PSU1': PowerSupply('PS1 Status', True, input_watts=100 if self.power_on else 5, input_volts=230),
                'PSU2': PowerSupply('PS2 Status', False, input_watts=0, input_volts=0),
            },
            alerts=[Alert('1', '2026-01-01T10:00:00+00:00', 'Critical',
                          'Power supply 2 is not receiving input power.', 'PSU0003')],
        )

    async def set_power(self, action: str) -> None:
        # A forced restart leaves a running server running; everything else but On stops it.
        self.power_on = action == POWER_ON or (action == POWER_FORCE_RESTART and self.power_on)
