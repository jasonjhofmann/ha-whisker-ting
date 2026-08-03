"""Tests for the coordinator's station-id candidate probe."""

from unittest.mock import AsyncMock, MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.whisker_ting.api import DeviceState
from custom_components.whisker_ting.const import CONF_STATION_IDS, DOMAIN
from custom_components.whisker_ting.coordinator import WhiskerDataUpdateCoordinator
from homeassistant.core import HomeAssistant


def _device() -> DeviceState:
    return DeviceState(
        serial_number="TG-0001",
        name="Test Ting",
        device_type="FireSensor",
        site_id=555,
        soc_serial_number="SOC-9",
        station_id="TG-0001",
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


def _manager(wait_results):
    """A fake WS manager whose wait_for_data pops scripted results."""
    manager = MagicMock()
    results = list(wait_results)
    manager.wait_for_data = AsyncMock(side_effect=lambda *a, **k: results.pop(0))
    manager.connect_device = AsyncMock(return_value=True)
    manager.disconnect_device = AsyncMock()
    return manager


async def test_probe_current_station_works(hass: HomeAssistant):
    """If the already-connected station produces data, persist it and stop."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    manager = _manager([True])
    coordinator = _coordinator(hass, entry, manager)
    device = _device()

    await coordinator._probe_station_id(device)

    assert coordinator._discovered_station_ids == {"TG-0001": "TG-0001"}
    assert entry.options[CONF_STATION_IDS] == {"TG-0001": "TG-0001"}
    manager.connect_device.assert_not_called()
    manager.disconnect_device.assert_not_called()


async def test_probe_rotates_to_site_id(hass: HomeAssistant):
    """Serial silent -> site id produces data -> persisted and adopted."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    # serial times out, site-id candidate delivers
    manager = _manager([False, True])
    coordinator = _coordinator(hass, entry, manager)
    device = _device()

    await coordinator._probe_station_id(device)

    assert device.station_id == "555"
    assert coordinator._discovered_station_ids == {"TG-0001": "555"}
    assert entry.options[CONF_STATION_IDS] == {"TG-0001": "555"}
    manager.disconnect_device.assert_awaited_with("TG-0001")
    manager.connect_device.assert_awaited_with(
        api_key="key", user_id=7, station_id="555"
    )


async def test_probe_all_candidates_fail_falls_back_to_serial(hass: HomeAssistant):
    """No candidate works: nothing persisted, serial reconnected for backoff."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    # current + site + soc + group all silent
    manager = _manager([False, False, False, False])
    coordinator = _coordinator(hass, entry, manager)
    device = _device()

    await coordinator._probe_station_id(device)

    assert coordinator._discovered_station_ids == {}
    assert CONF_STATION_IDS not in entry.options
    assert device.station_id == "TG-0001"
    # The last probed candidate was torn down and the serial reconnected so
    # the manager's capped backoff keeps retrying.
    assert manager.connect_device.await_args.kwargs["station_id"] == "TG-0001"


async def test_connect_prefers_persisted_station_id(hass: HomeAssistant):
    """A previously discovered station id is used on the next connect."""
    entry = MockConfigEntry(
        domain=DOMAIN, options={CONF_STATION_IDS: {"TG-0001": "555"}}
    )
    entry.add_to_hass(hass)
    manager = _manager([])
    coordinator = _coordinator(hass, entry, manager)
    device = _device()

    await coordinator._connect_websocket({"TG-0001": device})

    assert device.station_id == "555"
    manager.connect_device.assert_awaited_with(
        api_key="key", user_id=7, station_id="555"
    )
