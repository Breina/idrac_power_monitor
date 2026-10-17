"""System Event Log entries as an event entity, for automations and the logbook."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IdracConfigEntry
from .client import Alert
from .coordinator import IdracCoordinator
from .entity import IdracEntity, add_entities_as_they_appear

# Redfish severities, as event types
EVENT_TYPES = {'Critical': 'critical', 'Warning': 'warning', 'OK': 'ok'}


async def async_setup_entry(hass: HomeAssistant, entry: IdracConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = entry.runtime_data

    def build():
        if coordinator.data.alerts is not None:
            yield 'alert', lambda: IdracAlertEvent(coordinator)

    add_entities_as_they_appear(coordinator, async_add_entities, build)


class IdracAlertEvent(IdracEntity, EventEntity):
    """Fires once for each new entry in the iDRAC's System Event Log."""
    _attr_icon = 'mdi:alert-circle-outline'
    _attr_event_types = list(EVENT_TYPES.values())

    def __init__(self, coordinator: IdracCoordinator):
        super().__init__(coordinator, 'alert', 'Alert')

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Entries found by the poll that ran before this entity existed
        self._fire(self.coordinator.new_alerts)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._fire(self.coordinator.new_alerts)
        super()._handle_coordinator_update()

    @callback
    def _fire(self, alerts: list[Alert]) -> None:
        for alert in alerts:
            self._trigger_event(EVENT_TYPES[alert.severity], {
                'message': alert.message,
                'logged_at': alert.created,
                'log_id': alert.id,
                'message_id': alert.message_id,
            })
        if alerts:
            self.async_write_ha_state()
