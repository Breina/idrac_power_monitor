"""Event log entities share how they present log records."""
from __future__ import annotations

from typing import Any

from .client import SEVERITY_CRITICAL, SEVERITY_WARNING, LogEntry

# Attributes carry the newest records only: HA keeps attributes in the database
RECENT = 10
PROBLEM_SEVERITIES = (SEVERITY_WARNING, SEVERITY_CRITICAL)


def as_dict(entry: LogEntry) -> dict[str, Any]:
    return {
        'time': entry.created.isoformat() if entry.created else None,
        'severity': entry.severity,
        'message': entry.message,
    }


def problems(entries: list[LogEntry]) -> list[LogEntry]:
    return [entry for entry in entries if entry.severity in PROBLEM_SEVERITIES]
