"""Per power supply power and energy, as shares of what the server draws.

The iDRAC measures the whole server's input power (W) and energy (kWh). Per
power supply it only tells how the load is spread: the input current on
iDRAC 6 (ampReading, 0.1 A steps), the input power on Redfish. A current
times the nominal voltage is apparent power, not real power: 0.5 A x 240 V
on each PSU makes 240 VA on a server drawing 217 W. So each PSU gets its
share of the measured total instead, and PS 1 + PS 2 always equal the server.
The Energy dashboard needs that when each PSU is declared "included in" the
plug or UPS it hangs from: anything else counts part of the server twice.
"""
from __future__ import annotations

from typing import Any

from .client import IdracData


def shares(data: IdracData) -> dict[str, float]:
    """Each PSU's share of the server's load; empty when there is nothing to split.

    Proportional to the per-PSU readings while all of them are known and not
    all zero; otherwise (host off: no current is measured) an equal split
    between the PSUs the iDRAC lists. With no PSU listed (some powered-off
    servers answer without any sensor) the count is unknown, and nothing is
    guessed. iDRACs that report no per-PSU reading at all get no split.
    """
    supplies = data.power_supplies
    if not any(psu.has_load for psu in supplies.values()):
        return {}
    loads = [psu.load for psu in supplies.values()]
    if None not in loads and (total := sum(loads)) > 0:
        return {psu_id: psu.load / total for psu_id, psu in supplies.items()}
    return dict.fromkeys(supplies, 1 / len(supplies))


def split_power(data: IdracData, psu_shares: dict[str, float]) -> dict[str, float | None]:
    if data.power_watts is None:
        return dict.fromkeys(psu_shares)
    return {psu_id: round(data.power_watts * share, 1) for psu_id, share in psu_shares.items()}


class EnergySplit:
    """Splits the iDRAC's cumulative energy counter between PSUs, poll after poll.

    Each increase of the counter goes to the PSUs with the shares in force
    over that interval, those of the previous poll. The very first reading
    and a counter that went down (reset from the iDRAC UI) only set a new
    baseline. While no share is known the increase is held, and split once
    one is: the PSU totals always add up to what the counter counted.
    The state is saved, so that totals carry on across restarts.
    """

    def __init__(self, stored: dict[str, Any] | None = None):
        stored = stored or {}
        self.counter: float | None = stored.get('counter')
        self.shares: dict[str, float] = dict(stored.get('shares') or {})
        self.energy: dict[str, float] = dict(stored.get('energy') or {})

    def update(self, counter: float | None, psu_shares: dict[str, float]) -> bool:
        """Account for a new counter reading; True when the state changed."""
        before = self.as_dict()
        in_force = self.shares or psu_shares
        if counter is not None:
            if self.counter is None or counter < self.counter:
                self.counter = counter
            elif in_force:
                increase = counter - self.counter
                for psu_id, share in in_force.items():
                    self.energy[psu_id] = self.energy.get(psu_id, 0.0) + increase * share
                self.counter = counter
        self.shares = dict(psu_shares)
        return self.as_dict() != before

    def as_dict(self) -> dict[str, Any]:
        return {'counter': self.counter, 'shares': dict(self.shares), 'energy': dict(self.energy)}
