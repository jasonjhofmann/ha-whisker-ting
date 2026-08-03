"""The Whisker Ting integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WhiskerApiClient
from .const import (
    CONF_ALERT_NOTIFICATIONS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_ALERT_NOTIFICATIONS,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import WhiskerConfigEntry, WhiskerDataUpdateCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WhiskerConfigEntry) -> bool:
    """Set up Whisker Ting from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    notify_enabled = entry.options.get(
        CONF_ALERT_NOTIFICATIONS, DEFAULT_ALERT_NOTIFICATIONS
    )

    session = async_get_clientsession(hass)
    client = WhiskerApiClient(session, username, password)

    coordinator = WhiskerDataUpdateCoordinator(
        hass, client, session, scan_interval, notify_enabled
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass: HomeAssistant, entry: WhiskerConfigEntry) -> None:
    """Handle options update."""
    coordinator: WhiskerDataUpdateCoordinator = entry.runtime_data
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator.update_interval = timedelta(seconds=scan_interval)
    coordinator.notify_enabled = entry.options.get(
        CONF_ALERT_NOTIFICATIONS, DEFAULT_ALERT_NOTIFICATIONS
    )
    _LOGGER.debug(
        "Updated options: scan_interval=%s notify_enabled=%s",
        scan_interval,
        coordinator.notify_enabled,
    )


async def async_unload_entry(hass: HomeAssistant, entry: WhiskerConfigEntry) -> bool:
    """Unload a config entry."""
    # Shutdown the coordinator (disconnects WebSocket)
    coordinator: WhiskerDataUpdateCoordinator = entry.runtime_data
    await coordinator.async_shutdown()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
