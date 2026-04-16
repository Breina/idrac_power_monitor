"""Legacy iDRAC power polling via IPMI/DCMI."""

from __future__ import annotations

import logging
from typing import Callable

from pyghmi.ipmi.command import Command

from .const import (
    JSON_MANUFACTURER,
    JSON_MODEL,
    JSON_NAME,
    JSON_SERIAL_NUMBER,
)
from .idrac_rest import CannotConnect, InvalidAuth

_LOGGER = logging.getLogger(__name__)


class IdracLegacyIpmi:
    """Minimal legacy client for iDRAC6-style IPMI power polling."""

    supports_thermals = False
    supports_status = False
    supports_power_control = False
    supports_energy = False

    def __init__(self, host: str, username: str, password: str, interval: int):
        self.host = host
        self.auth = (username, password)
        self.interval = interval

        self.callback_thermals: list[Callable[[dict | None], None]] = []
        self.callback_status: list[Callable[[bool | None], None]] = []
        self.callback_power_usage: list[Callable[[int | None], None]] = []
        self.callback_energy_consumption: list[Callable[[float | None], None]] = []

        self.thermal_values: dict = {}
        self.status: bool | None = None
        self.power_usage: int | None = None
        self.energy_consumption: float | None = None

        self._ipmi: Command | None = None

    def _get_ipmi(self) -> Command:
        if self._ipmi is None:
            self._ipmi = Command(
                bmc=self.host,
                userid=self.auth[0],
                password=self.auth[1],
                port=623,
            )
        return self._ipmi

    def _probe_power_watts(self) -> int:
        """Read current system power in watts.

        First try DCMI via pyghmi's helper. If that is not available on the
        target, fall back to scanning normal IPMI sensor data for a watts-based
        system power sensor.
        """
        ipmi = self._get_ipmi()

        try:
            watts = ipmi.get_system_power_watts()
            if watts is not None:
                return int(watts)
        except Exception as err:
            _LOGGER.debug("DCMI power read failed for %s: %s", self.host, err)

        try:
            for reading in ipmi.get_sensor_data():
                if getattr(reading, "unavailable", 0):
                    continue

                name = (getattr(reading, "name", "") or "").strip().lower()
                units = (getattr(reading, "units", "") or "").strip().lower()
                value = getattr(reading, "value", None)

                if value is None or units != "w":
                    continue

                if name in {"pwr consumption", "power consumption", "system level"}:
                    return int(round(float(value)))
        except Exception as err:
            _LOGGER.debug("Sensor-based power read failed for %s: %s", self.host, err)

        raise CannotConnect(
            f"No IPMI power reading available on {self.host}. "
            "Check that IPMI over LAN is enabled and that platform power monitoring is supported."
        )

    def get_device_info(self) -> dict | None:
        try:
            # Validate connectivity/auth and capability up front.
            self._probe_power_watts()
        except Exception as err:
            lowered = str(err).lower()
            if "password" in lowered or "username" in lowered or "unauthorized" in lowered:
                raise InvalidAuth()
            raise

        return {
            JSON_NAME: self.host,
            JSON_MANUFACTURER: "Dell",
            JSON_MODEL: "iDRAC6 (legacy IPMI)",
            JSON_SERIAL_NUMBER: self.host,
        }

    def get_firmware_version(self) -> str | None:
        return "legacy-ipmi"

    def register_callback_thermals(self, callback: Callable[[dict | None], None]) -> None:
        self.callback_thermals.append(callback)

    def register_callback_status(self, callback: Callable[[bool | None], None]) -> None:
        self.callback_status.append(callback)

    def register_callback_power_usage(self, callback: Callable[[int | None], None]) -> None:
        self.callback_power_usage.append(callback)

    def register_callback_energy_consumption(self, callback: Callable[[float | None], None]) -> None:
        self.callback_energy_consumption.append(callback)

    def idrac_reset(self, reset_type: str):
        raise CannotConnect(f"Power control is not implemented for legacy IPMI mode ({reset_type}).")

    def update_thermals(self) -> dict:
        return {}

    def update_status(self):
        return None

    def update_power_usage(self):
        try:
            new_power_usage = self._probe_power_watts()
        except Exception as err:
            _LOGGER.debug("Couldn't update %s legacy IPMI power usage: %s", self.host, err)
            for callback in self.callback_power_usage:
                callback(None)
            return

        if new_power_usage != self.power_usage:
            self.power_usage = new_power_usage
            for callback in self.callback_power_usage:
                callback(self.power_usage)
