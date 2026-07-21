"""Tests for Ting notification parsing, coordinator wiring, and alert entities."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.whisker_ting.api import TingNotification, WhiskerApiClient


def _load(name: str):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def test_parse_notifications():
    raw = _load("notifications.json")
    # _parse_notification is a staticmethod: no __init__/instance needed for parse.
    parsed = [WhiskerApiClient._parse_notification(n) for n in raw]
    assert len(parsed) == 4
    outage = next(n for n in parsed if n.event_type == "PowerOutage")
    assert isinstance(outage, TingNotification)
    assert outage.serial_number == "TG-0001"
    assert outage.title == "Power Outage"
    assert outage.timestamp is not None
    assert outage.timestamp.tzinfo is not None
    assert outage.sent_utc is not None
    assert outage.is_acknowledged is False
