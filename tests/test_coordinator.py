"""Coordinator stream handling: serial StationId, one-shot start, liveness.

The hub answers every InitializeStreaming with an error-free Completion no
matter what StationId is supplied, so the old candidate-rotation probe
could never discriminate; it only ever subscribed to the wrong station.
These tests lock in that the serial is used, that station ids persisted by
that probe are ignored, and that the manager (not the coordinator) owns
every retry.
"""

from unittest.mock import AsyncMock, MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.whisker_ting.api import DeviceState
from custom_components.whisker_ting.const import CONF_STATION_IDS, DOMAIN
from custom_components.whisker_ting.coordinator import WhiskerDataUpdateCoordinator
from homeassistant.core import HomeAssistant


def _device(serial: str = "TG-0001") -> DeviceState:
    return DeviceState(
        serial_number=serial,
        name="Test Ting",
        device_type="FireSensor",
        site_id=555,
        soc_serial_number="SOC-9",
        station_id=serial,
        group_id=42,
    )


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry, manager):
    client = MagicMock()
    client.api_key = "key"
    client.user_id = 7
    coordinator = WhiskerDataUpdateCoordinator(
        hass, client, MagicMock(), config_entry=entry
    )
    coordinator._ws_manager = manager
    return coordinator


def _manager():
    manager = MagicMock()
    manager.wait_for_data = AsyncMock(return_value=False)
    manager.connect_device = AsyncMock(return_value=True)
    manager.disconnect_device = AsyncMock()
    return manager


async def test_connect_uses_device_serial_as_station_id(hass: HomeAssistant):
    """StationId must be the sensor serial, matching the official app."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    manager = _manager()
    coordinator = _coordinator(hass, entry, manager)
    device = _device()

    await coordinator._connect_websocket({"TG-0001": device})

    assert device.station_id == "TG-0001"
    manager.connect_device.assert_awaited_with(
        api_key="key", user_id=7, station_id="TG-0001"
    )


async def test_persisted_probe_station_id_is_ignored(hass: HomeAssistant):
    """A site/group id stored by the retired probe must not override the serial."""
    entry = MockConfigEntry(
        domain=DOMAIN, options={CONF_STATION_IDS: {"TG-0001": "555"}}
    )
    entry.add_to_hass(hass)
    manager = _manager()
    coordinator = _coordinator(hass, entry, manager)
    device = _device()

    await coordinator._connect_websocket({"TG-0001": device})

    assert device.station_id == "TG-0001"
    manager.connect_device.assert_awaited_once_with(
        api_key="key", user_id=7, station_id="TG-0001"
    )


async def test_one_failed_device_does_not_strand_the_others(hass: HomeAssistant):
    """Regression: the first success used to stop every later attempt.

    Every device is handed to the manager exactly once (the manager retries
    failures itself), and a later poll does not reconnect anything.
    """
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    manager = _manager()
    manager.connect_device = AsyncMock(side_effect=[True, False, RuntimeError("boom")])
    coordinator = _coordinator(hass, entry, manager)
    data = {s: _device(s) for s in ("TG-0001", "TG-0002", "TG-0003")}

    await coordinator._connect_websocket(data)
    await coordinator._connect_websocket(data)

    assert sorted(
        call.kwargs["station_id"] for call in manager.connect_device.await_args_list
    ) == ["TG-0001", "TG-0002", "TG-0003"]


async def test_missing_credentials_defers_the_stream_start(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    manager = _manager()
    coordinator = _coordinator(hass, entry, manager)
    coordinator.client.api_key = None

    await coordinator._connect_websocket({"TG-0001": _device()})
    manager.connect_device.assert_not_called()

    coordinator.client.api_key = "key"
    await coordinator._connect_websocket({"TG-0001": _device()})
    manager.connect_device.assert_awaited_once()


async def test_voltage_liveness_requires_an_open_stream(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    manager = _manager()
    manager.is_streaming = MagicMock(return_value=False)
    coordinator = _coordinator(hass, entry, manager)

    assert coordinator.voltage_is_live("TG-0001") is False
    manager.is_streaming.assert_called_once_with("TG-0001")


async def test_connection_lost_refreshes_entities(hass: HomeAssistant):
    """A dropped stream re-evaluates availability without waiting for a poll."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _manager())
    listener = MagicMock()
    unsubscribe = coordinator.async_add_listener(listener)

    coordinator._handle_connection_lost("TG-0001")
    listener.assert_not_called()  # nothing to refresh before the first poll

    coordinator.data = {"TG-0001": _device()}
    coordinator._handle_connection_lost("TG-0001")
    listener.assert_called_once()
    unsubscribe()
