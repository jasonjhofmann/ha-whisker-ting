"""Data coordinator for Whisker Ting."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    DeviceState,
    TingNotification,
    VoltageReading,
    WhiskerApiClient,
    WhiskerApiError,
    WhiskerAuthError,
)
from .const import (
    CONF_STATION_IDS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VOLTAGE_PUBLISH_INTERVAL,
    DOMAIN,
    INSIGNIFICANT_NOTIFICATION_TYPES,
)
from .websocket import VoltageData, WhiskerWebSocketManager

if TYPE_CHECKING:
    import aiohttp

_LOGGER = logging.getLogger(__name__)

# Station-id candidates for the streaming subscription, in order of
# likelihood. The serial number works for most accounts; when the server
# rejects it (or stays silent), the coordinator probes the alternates and
# persists the first one that produces voltage data.
_STATION_ID_CANDIDATES = (
    lambda d: d.serial_number,
    lambda d: str(d.site_id) if d.site_id else None,
    lambda d: d.soc_serial_number,
    lambda d: str(d.group_id) if d.group_id else None,
)

# How long to wait for voltage data after subscribing before moving to the
# next candidate station id.
_PROBE_TIMEOUT = 35.0  # seconds


class WhiskerDataUpdateCoordinator(DataUpdateCoordinator[dict[str, DeviceState]]):
    """Class to manage fetching Whisker Ting data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: WhiskerApiClient,
        session: aiohttp.ClientSession,
        update_interval_seconds: int = DEFAULT_SCAN_INTERVAL,
        notify_enabled: bool = False,
        voltage_publish_interval: int = DEFAULT_VOLTAGE_PUBLISH_INTERVAL,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval_seconds),
            config_entry=config_entry,
        )
        self.client = client
        self._session = session
        self._last_update_success: bool | None = None
        self._ws_manager: WhiskerWebSocketManager | None = None
        self._ws_connected = False
        self.notify_enabled = notify_enabled
        self.voltage_publish_interval = voltage_publish_interval
        self._seen_notification_ids: set[str] = set()
        self._notifications_seeded = False
        # Discovered station_ids keyed by device serial number, persisted in
        # config-entry options so restarts skip the probe.
        self._discovered_station_ids: dict[str, str] = dict(
            (config_entry.options.get(CONF_STATION_IDS) or {}) if config_entry else {}
        )
        self._probe_tasks: dict[str, asyncio.Task] = {}
        # After a full candidate rotation fails, wait this long before
        # probing again (the manager's capped backoff keeps retrying the
        # serial in the meantime).
        self._probe_cooldown_until: dict[str, datetime] = {}

    async def _async_setup(self) -> None:
        """One-time setup: create the WebSocket manager."""
        self._ws_manager = WhiskerWebSocketManager(
            session=self._session,
            on_voltage_update=self._handle_voltage_update,
            publish_interval=float(self.voltage_publish_interval),
        )

    def set_voltage_publish_interval(self, seconds: int) -> None:
        """Apply a new voltage publish interval (options update)."""
        self.voltage_publish_interval = seconds
        if self._ws_manager:
            self._ws_manager.publish_interval = float(seconds)

    def voltage_is_live(self, device_id: str) -> bool:
        """Return True if a fresh real-time voltage stream exists for a device.

        ``device_id`` is the device serial; the manager keys freshness by
        station id, which differs from the serial on probed accounts.
        """
        if not self._ws_manager:
            return False
        station_id = self._discovered_station_ids.get(device_id, device_id)
        return self._ws_manager.is_data_fresh(station_id)

    @callback
    def _handle_voltage_update(
        self, station_id: str, voltage_data: VoltageData
    ) -> None:
        """Handle a (manager-throttled) real-time voltage update.

        The WebSocket manager rate-limits this callback to the configured
        publish interval, so every call fans out to HA state directly.
        """
        if self.data is None:
            return

        # Find the device with this station_id
        for device_state in self.data.values():
            if device_state.station_id == station_id:
                device_state.voltage = VoltageReading(
                    voltage=voltage_data.voltage,
                    voltage_hi=voltage_data.voltage_hi,
                    voltage_lo=voltage_data.voltage_lo,
                    average_peaks_max=voltage_data.average_peaks_max,
                )
                # Notify entities WITHOUT async_set_updated_data: that call
                # reschedules the poll timer, so pushes arriving faster
                # than scan_interval would starve the REST poll entirely.
                self.async_update_listeners()
                break

    async def _connect_websocket(self, data: dict[str, DeviceState]) -> None:
        """Connect to WebSocket for real-time updates."""
        if not data or self._ws_connected:
            return

        # Get api_key and user_id from the client
        api_key = self.client.api_key
        user_id = self.client.user_id

        if not api_key or not user_id:
            _LOGGER.debug("No api_key or user_id, skipping WebSocket connection")
            return

        # Connect to each device's WebSocket stream
        for device_id, device_state in data.items():
            # Prefer a previously discovered (probed) station id
            known = self._discovered_station_ids.get(device_state.serial_number)
            if known:
                device_state.station_id = known
            if device_state.station_id:
                try:
                    connected = await self._ws_manager.connect_device(
                        api_key=api_key,
                        user_id=user_id,
                        station_id=device_state.station_id,
                    )
                    if connected:
                        _LOGGER.info(
                            "Connected to WebSocket for device %s (station %s)",
                            device_id,
                            device_state.station_id,
                        )
                        self._ws_connected = True
                except Exception as err:  # noqa: BLE001 - one device's failure must not abort the rest
                    _LOGGER.warning(
                        "Failed to connect WebSocket for device %s: %s",
                        device_id,
                        err,
                    )

    def _maybe_start_station_probe(self, device_state: DeviceState) -> None:
        """Probe alternate station ids in the background if none has worked.

        Some accounts' streaming subscriptions reject the device serial and
        require a different identifier (site id, SoC serial, or group id).
        The probe rotates candidates, and the first one that produces
        voltage data is persisted to config-entry options so restarts
        connect directly.
        """
        serial = device_state.serial_number
        if serial in self._discovered_station_ids:
            return  # already probed successfully
        task = self._probe_tasks.get(serial)
        if task and not task.done():
            return  # probe already running
        cooldown = self._probe_cooldown_until.get(serial)
        if cooldown and dt_util.utcnow() < cooldown:
            return  # a full rotation just failed; backoff is retrying
        self._probe_tasks[serial] = self.hass.async_create_background_task(
            self._probe_station_id(device_state),
            name=f"whisker_ting station probe {serial}",
        )

    async def _probe_station_id(self, device_state: DeviceState) -> None:
        """Try each candidate station id until one produces voltage data."""
        serial = device_state.serial_number
        api_key = self.client.api_key
        user_id = self.client.user_id
        if not api_key or not user_id or not self._ws_manager:
            return

        # Give the currently connected candidate (normally the serial) the
        # full probe window first — the 5 s startup wait is often too short.
        current = device_state.station_id
        if current and await self._ws_manager.wait_for_data(
            current, timeout=_PROBE_TIMEOUT
        ):
            self._persist_station_id(serial, current)
            return

        for candidate_fn in _STATION_ID_CANDIDATES:
            candidate = candidate_fn(device_state)
            if not candidate or candidate == current:
                continue

            _LOGGER.debug(
                "Probing station id candidate '%s' for device %s",
                candidate,
                serial,
            )
            if current:
                await self._ws_manager.disconnect_device(current)
            connected = await self._ws_manager.connect_device(
                api_key=api_key, user_id=user_id, station_id=candidate
            )
            current = candidate if connected else None
            if not connected:
                continue

            device_state.station_id = candidate
            if await self._ws_manager.wait_for_data(candidate, timeout=_PROBE_TIMEOUT):
                _LOGGER.info(
                    "Discovered working station id '%s' for device %s",
                    candidate,
                    serial,
                )
                self._persist_station_id(serial, candidate)
                return

        # No candidate produced data. Fall back to the serial so the
        # manager's capped backoff keeps retrying — the server-side
        # streaming-authorization state has been observed to clear later.
        _LOGGER.warning(
            "No station id candidate produced voltage data for device %s; "
            "will keep retrying with '%s'",
            serial,
            device_state.serial_number,
        )
        if current and current != device_state.serial_number:
            await self._ws_manager.disconnect_device(current)
            await self._ws_manager.connect_device(
                api_key=api_key, user_id=user_id, station_id=device_state.serial_number
            )
        device_state.station_id = device_state.serial_number
        self._probe_cooldown_until[serial] = dt_util.utcnow() + timedelta(minutes=30)

    def _persist_station_id(self, serial: str, station_id: str) -> None:
        """Persist a discovered station id to config-entry options."""
        self._discovered_station_ids[serial] = station_id
        entry = self.config_entry
        if entry is None:
            return
        stored = entry.options.get(CONF_STATION_IDS) or {}
        if stored.get(serial) == station_id:
            return
        self.hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_STATION_IDS: {**stored, serial: station_id},
            },
        )

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        for task in self._probe_tasks.values():
            if not task.done():
                task.cancel()
        self._probe_tasks.clear()
        if self._ws_manager:
            await self._ws_manager.disconnect_all()
            self._ws_connected = False
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, DeviceState]:
        """Fetch data from the API."""
        try:
            data = await self.client.get_all_device_states()

            # The API parser sets station_id to the serial; re-apply any
            # probed station id on EVERY poll — the DeviceState objects are
            # rebuilt each cycle, and _handle_voltage_update matches
            # incoming samples against device_state.station_id.
            for device_state in data.values():
                known = self._discovered_station_ids.get(device_state.serial_number)
                if known:
                    device_state.station_id = known

            # Preserve existing voltage data from WebSocket
            if self.data:
                for device_id, device_state in data.items():
                    existing = self.data.get(device_id)
                    if existing and existing.voltage.voltage > 0:
                        device_state.voltage = existing.voltage

            if self._last_update_success is False:
                _LOGGER.info("Connection to Whisker Ting API restored")
            self._last_update_success = True

            # Connect WebSocket on first fetch and wait for data
            if not self._ws_connected:
                await self._connect_websocket(data)
                # Wait for actual voltage data to arrive (not arbitrary sleep)
                if self._ws_connected and self._ws_manager:
                    # Wait for data from all devices in parallel
                    wait_tasks = [
                        self._ws_manager.wait_for_data(
                            device_state.station_id, timeout=5.0
                        )
                        for device_state in data.values()
                        if device_state.station_id
                    ]
                    if wait_tasks:
                        await asyncio.gather(*wait_tasks)
                    # Update data with voltage readings received
                    for device_state in data.values():
                        if device_state.station_id:
                            voltage_data = self._ws_manager.get_voltage_data(
                                device_state.station_id
                            )
                            if voltage_data:
                                device_state.voltage = VoltageReading(
                                    voltage=voltage_data.voltage,
                                    voltage_hi=voltage_data.voltage_hi,
                                    voltage_lo=voltage_data.voltage_lo,
                                    average_peaks_max=voltage_data.average_peaks_max,
                                )
                            else:
                                # No data on the default station id yet —
                                # probe the alternates in the background.
                                self._maybe_start_station_probe(device_state)
            elif self._ws_manager:
                # Already connected: re-arm the probe (cooldown-guarded) for
                # any station that has never produced data — a silently
                # unauthorized subscription, or a probe that previously
                # failed and may succeed now that the server-side state has
                # cleared.
                for device_state in data.values():
                    if (
                        device_state.station_id
                        and self._ws_manager.get_voltage_data(device_state.station_id)
                        is None
                    ):
                        self._maybe_start_station_probe(device_state)

            # Fetch notifications (best-effort; users poll stays authoritative).
            try:
                notifications = await self.client.get_notifications()
            except WhiskerApiError as err:
                # Scope intentional: also catches WhiskerAuthError, but the users poll above already surfaces persistent auth failures via ConfigEntryAuthFailed.
                _LOGGER.debug("Notifications fetch failed: %s", err)
                notifications = None

            if notifications is not None:
                by_serial: dict[str, list[TingNotification]] = {}
                for note in notifications:
                    by_serial.setdefault(note.serial_number, []).append(note)
                for device_state in data.values():
                    device_state.notifications = by_serial.get(
                        device_state.serial_number, []
                    )
                self._process_new_notifications(notifications)
            elif self.data:
                # Preserve the previous poll's notifications on a transient failure.
                for device_id, device_state in data.items():
                    existing = self.data.get(device_id)
                    if existing:
                        device_state.notifications = existing.notifications

        except WhiskerAuthError as err:
            self._last_update_success = False
            raise ConfigEntryAuthFailed(
                "Authentication failed - credentials may have changed"
            ) from err
        except WhiskerApiError as err:
            if self._last_update_success is not False:
                _LOGGER.warning("Unable to connect to Whisker Ting API: %s", err)
            self._last_update_success = False
            raise UpdateFailed(
                f"Error communicating with Whisker Ting API: {err}"
            ) from err
        else:
            return data

    def _process_new_notifications(self, notifications: list[TingNotification]) -> None:
        """Seed on first poll; on later polls, post opt-in HA notifications for new significant alerts."""
        all_ids = {n.id for n in notifications}
        if not self._notifications_seeded:
            self._seen_notification_ids = all_ids
            self._notifications_seeded = True
            return
        new = [n for n in notifications if n.id not in self._seen_notification_ids]
        self._seen_notification_ids |= all_ids
        if not (self.notify_enabled and new):
            return
        for note in new:
            if note.event_type in INSIGNIFICANT_NOTIFICATION_TYPES:
                continue
            persistent_notification.async_create(
                self.hass,
                message=note.message or "",
                title=f"Ting: {note.title}" if note.title else "Ting alert",
                notification_id=f"{DOMAIN}_{note.id}",
            )


type WhiskerConfigEntry = ConfigEntry[WhiskerDataUpdateCoordinator]
