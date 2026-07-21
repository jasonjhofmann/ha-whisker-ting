"""Tests for Ting notification parsing, coordinator wiring, and alert entities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.whisker_ting.api import (
    TingNotification,
    WhiskerApiClient,
    WhiskerApiError,
)
import homeassistant.util.dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


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


def test_device_named_by_site():
    raw = _load("user_data_multi.json")
    parser = WhiskerApiClient.__new__(WhiskerApiClient)
    user = WhiskerApiClient._parse_user_data(parser, raw)
    by_serial = {d.serial_number: d for d in user.devices}
    assert by_serial["TG-0001"].site_name == "Home - Kitchen"
    assert by_serial["TG-0002"].site_name == "Home - Bedroom"


@pytest.fixture
def mock_ws_manager():
    manager = MagicMock()
    manager.connect_device = AsyncMock(return_value=True)
    manager.wait_for_data = AsyncMock(return_value=True)
    manager.get_voltage_data = MagicMock(return_value=None)
    manager.is_data_fresh = MagicMock(return_value=True)
    manager.disconnect_all = AsyncMock()
    with patch(
        "custom_components.whisker_ting.coordinator.WhiskerWebSocketManager",
        return_value=manager,
    ):
        yield manager


def _notif(**kw):
    return TingNotification(**kw)


async def test_coordinator_attaches_notifications(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    mock_client.get_notifications.return_value = [
        _notif(
            id="a",
            event_type="PowerOutage",
            serial_number="TG-0001",
            sent_utc=dt_util.utcnow(),
            timestamp=dt_util.utcnow(),
        ),
        _notif(
            id="b",
            event_type="Sag",
            serial_number="OTHER",
            sent_utc=dt_util.utcnow(),
            timestamp=dt_util.utcnow(),
        ),
    ]
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    device = mock_config_entry.runtime_data.data["TG-0001"]
    assert [n.id for n in device.notifications] == ["a"]  # only this device's


async def test_notifications_preserved_on_transient_failure(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    mock_client.get_notifications.return_value = [
        _notif(
            id="a",
            event_type="PowerOutage",
            serial_number="TG-0001",
            sent_utc=dt_util.utcnow(),
            timestamp=dt_util.utcnow(),
        ),
    ]
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    assert [n.id for n in coordinator.data["TG-0001"].notifications] == ["a"]

    mock_client.get_notifications.side_effect = WhiskerApiError("x")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert [n.id for n in coordinator.data["TG-0001"].notifications] == ["a"]
    assert coordinator.last_update_success is True


async def test_auto_notify_posts_only_when_enabled(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    t0 = dt_util.utcnow()
    mock_client.get_notifications.return_value = []  # seed: no notifications
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    coordinator.notify_enabled = True

    # A new significant alert + a new brownout appear on the next poll.
    mock_client.get_notifications.return_value = [
        _notif(
            id="new-outage",
            event_type="PowerOutage",
            title="Power Outage",
            message="out",
            serial_number="TG-0001",
            sent_utc=t0,
            timestamp=t0,
        ),
        _notif(
            id="new-sag",
            event_type="Sag",
            title="Brownout",
            message="sag",
            serial_number="TG-0001",
            sent_utc=t0,
            timestamp=t0,
        ),
    ]

    # Patch-and-assert the call rather than depending on persistent_notification
    # internals (version-robust across HA releases).
    with patch(
        "custom_components.whisker_ting.coordinator.persistent_notification.async_create"
    ) as create:
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    create.assert_called_once_with(
        hass,
        message="out",
        title="Ting: Power Outage",
        notification_id="whisker_ting_new-outage",
    )


async def test_auto_notify_disabled_does_not_post(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    t0 = dt_util.utcnow()
    mock_client.get_notifications.return_value = []  # seed: no notifications
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    assert coordinator.notify_enabled is False  # opt-in: off by default

    # A new significant alert appears on the next poll.
    mock_client.get_notifications.return_value = [
        _notif(
            id="new-outage",
            event_type="PowerOutage",
            title="Power Outage",
            message="out",
            serial_number="TG-0001",
            sent_utc=t0,
            timestamp=t0,
        ),
    ]

    with patch(
        "custom_components.whisker_ting.coordinator.persistent_notification.async_create"
    ) as create:
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    create.assert_not_called()
