"""The Whisker Ting integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
    format_mac,
)

from .api import WhiskerApiClient, _reverse_mac
from .const import (
    CONF_ALERT_NOTIFICATIONS,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_STATION_IDS,
    CONF_USERNAME,
    CONF_VOLTAGE_PUBLISH_INTERVAL,
    DEFAULT_ALERT_NOTIFICATIONS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VOLTAGE_PUBLISH_INTERVAL,
    DOMAIN,
)
from .coordinator import WhiskerConfigEntry, WhiskerDataUpdateCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: WhiskerConfigEntry) -> bool:
    """Migrate a config entry to refresh-token-capable schema version 2.

    Version 1 entries retain their legacy password here. Runtime setup replaces
    it with a refresh token only after a successful first coordinator refresh,
    so a failed authentication cannot strand the entry without credentials.

    The migration also drops station ids persisted by the retired 3.0.x
    probe. The probe could not tell a wrong StationId from a quiet stream
    and stored site or group ids; the sensor serial is always correct.
    """
    if entry.version == 2:
        return True

    if entry.version != 1:
        _LOGGER.error(
            "Cannot migrate Whisker Ting config entry from unsupported version %s",
            entry.version,
        )
        return False

    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    has_password = isinstance(password, str) and bool(password)
    has_refresh_token = isinstance(refresh_token, str) and bool(refresh_token)
    if (
        not isinstance(username, str)
        or not username
        or not (has_password or has_refresh_token)
    ):
        _LOGGER.error("Cannot migrate Whisker Ting config entry with invalid data")
        return False

    options = {k: v for k, v in entry.options.items() if k != CONF_STATION_IDS}
    hass.config_entries.async_update_entry(entry, options=options, version=2)
    _LOGGER.info("Migrated Whisker Ting config entry schema to version 2")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WhiskerConfigEntry) -> bool:
    """Set up Whisker Ting from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data.get(CONF_PASSWORD)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    notify_enabled = entry.options.get(
        CONF_ALERT_NOTIFICATIONS, DEFAULT_ALERT_NOTIFICATIONS
    )
    voltage_publish_interval = entry.options.get(
        CONF_VOLTAGE_PUBLISH_INTERVAL, DEFAULT_VOLTAGE_PUBLISH_INTERVAL
    )

    session = async_get_clientsession(hass)

    def persist_rotated_refresh_token(rotated_refresh_token: str) -> None:
        """Persist a refresh token Cognito rotated during a later renewal."""
        if entry.data.get(CONF_REFRESH_TOKEN) == rotated_refresh_token:
            return
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_REFRESH_TOKEN: rotated_refresh_token},
        )

    client = WhiskerApiClient(
        session,
        username,
        password=password,
        refresh_token=refresh_token,
        on_refresh_token_update=persist_rotated_refresh_token,
    )

    coordinator = WhiskerDataUpdateCoordinator(
        hass,
        client,
        session,
        scan_interval,
        notify_enabled,
        voltage_publish_interval,
        config_entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()

    # Replace legacy plaintext-password storage only after the first coordinator
    # refresh proves authentication succeeded and produced a usable refresh token.
    # Keeping the password until this point ensures a failed upgrade remains
    # recoverable through Home Assistant's normal retry/reauth paths.
    if CONF_PASSWORD in entry.data and client.refresh_token:
        new_data = {
            **entry.data,
            CONF_REFRESH_TOKEN: client.refresh_token,
        }
        new_data.pop(CONF_PASSWORD, None)
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.info("Migrated Whisker Ting entry to refresh-token authentication")

    entry.runtime_data = coordinator

    _async_cleanup_stale_mac_connections(hass, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


def _async_cleanup_stale_mac_connections(
    hass: HomeAssistant, coordinator: WhiskerDataUpdateCoordinator
) -> None:
    """Drop byte-reversed MAC connections registered by older versions.

    Builds before 1.2.0 registered the Wi-Fi MAC — and before 3.0.1 the
    Bluetooth MAC — in the API's reversed byte order. The device registry
    only ever merges connections, so the stale reversed rows sat alongside
    the corrected ones forever. Remove exactly the reversed forms of the
    currently known MACs; any other connection (including ones shared with
    other integrations, e.g. a network integration merged via the correct
    MAC) is left untouched.
    """
    registry = dr.async_get(hass)
    for device_state in (coordinator.data or {}).values():
        device = registry.async_get_device(
            identifiers={(DOMAIN, device_state.serial_number)}
        )
        if device is None:
            continue

        stale: set[tuple[str, str]] = set()
        for conn_type, mac in (
            (CONNECTION_NETWORK_MAC, device_state.wifi_mac_address),
            (CONNECTION_BLUETOOTH, device_state.bluetooth_mac_address),
        ):
            if not mac:
                continue
            reversed_mac = _reverse_mac(mac)
            if reversed_mac and reversed_mac != mac:
                stale.add((conn_type, format_mac(reversed_mac)))

        to_remove = stale & device.connections
        if to_remove:
            _LOGGER.info(
                "Removing stale reversed MAC connections from device %s: %s",
                device_state.serial_number,
                sorted(to_remove),
            )
            registry.async_update_device(
                device.id, new_connections=device.connections - to_remove
            )


async def async_options_updated(hass: HomeAssistant, entry: WhiskerConfigEntry) -> None:
    """Handle options update."""
    coordinator: WhiskerDataUpdateCoordinator = entry.runtime_data
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator.update_interval = timedelta(seconds=scan_interval)
    coordinator.notify_enabled = entry.options.get(
        CONF_ALERT_NOTIFICATIONS, DEFAULT_ALERT_NOTIFICATIONS
    )
    coordinator.set_voltage_publish_interval(
        entry.options.get(
            CONF_VOLTAGE_PUBLISH_INTERVAL, DEFAULT_VOLTAGE_PUBLISH_INTERVAL
        )
    )
    _LOGGER.debug(
        "Updated options: scan_interval=%s notify_enabled=%s "
        "voltage_publish_interval=%s",
        scan_interval,
        coordinator.notify_enabled,
        coordinator.voltage_publish_interval,
    )


async def async_unload_entry(hass: HomeAssistant, entry: WhiskerConfigEntry) -> bool:
    """Unload a config entry."""
    # Shutdown the coordinator (disconnects WebSocket)
    coordinator: WhiskerDataUpdateCoordinator = entry.runtime_data
    await coordinator.async_shutdown()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
