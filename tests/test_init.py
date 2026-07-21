"""Setup / unload tests for the Whisker Ting integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.whisker_ting.api import WhiskerAuthError, WhiskerConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


@pytest.fixture
def mock_ws_manager():
    """Patch the WebSocket manager so setup is hermetic (no real socket)."""
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


async def test_setup_and_unload(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert "TG-0001" in mock_config_entry.runtime_data.data

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_ws_manager.disconnect_all.assert_awaited()


async def test_setup_auth_failed_triggers_reauth(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    mock_client.get_all_device_states.side_effect = WhiskerAuthError("bad")
    mock_client.test_connection.return_value = False
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_setup_cannot_connect_retries(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    mock_client.get_all_device_states.side_effect = WhiskerConnectionError("down")
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_ws_manager_created_on_setup(
    hass: HomeAssistant, mock_client, mock_config_entry, mock_ws_manager
):
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.runtime_data._ws_manager is mock_ws_manager
