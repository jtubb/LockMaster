"""DataUpdateCoordinator for LockMaster."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_LOCKS, DOMAIN
from .manager import LockManager

_LOGGER = logging.getLogger(__name__)

# Lock event fields from zigbee2mqtt
LOCK_EVENT_ACTION = "action"
LOCK_EVENT_USER = "action_user"
LOCK_EVENT_SOURCE = "action_source_name"

# Master user code in zigbee2mqtt
MASTER_USER_CODE = "65535"

# Time to wait for all event fields to arrive before firing event
LOCK_EVENT_DEBOUNCE_SECONDS = 0.5

# Suppress identical (action, user, source) repeats within this window.
# Kwikset locks in a stuck state retransmit the same OperationEventNotification
# every ~30s; a legitimate human repeat of the exact same action+user+source
# within a minute is vanishingly unlikely, so 60s is a safe gate.
LOCK_EVENT_DEDUP_SECONDS = 60


@dataclass
class PendingLockEvent:
    """Pending lock event data being collected."""

    action: str | None = None
    user: str | None = None
    source: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    timer_task: asyncio.Task | None = None

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.storage"


class LockMasterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """LockMaster data update coordinator."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
        )
        self.config_entry = entry
        self.manager = LockManager()
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._unsubscribe_mqtt: list[callable] = []

        # Pending lock events being collected (key: lock name)
        self._pending_lock_events: dict[str, PendingLockEvent] = {}

        # Last fired event per device, for duplicate suppression.
        # Value is ((action, user, source), timestamp).
        self._last_fired_events: dict[
            str, tuple[tuple[str | None, str | None, str | None], datetime]
        ] = {}

        # Set up callbacks
        self.manager.set_mqtt_publish(self._mqtt_publish)
        self.manager.set_update_callback(self._on_manager_update)

    async def _mqtt_publish(self, topic: str, payload: str) -> None:
        """Publish MQTT message."""
        _LOGGER.debug("MQTT publish: %s -> %s", topic, payload)
        await mqtt.async_publish(self.hass, topic, payload)

    async def _on_manager_update(self) -> None:
        """Handle manager data update."""
        await self._save_state()
        self.async_set_updated_data(self._get_data())

    def _get_data(self) -> dict[str, Any]:
        """Get current data."""
        return {
            "users": {uid: user.to_dict() for uid, user in self.manager.users.items()},
            "locks": list(self.manager.locks.keys()),
            "lock_slots": {name: lock.slot_count for name, lock in self.manager.locks.items()},
            "max_slots": self.manager.max_slots,
            "enabled_count": len(self.manager.get_enabled_users()),
            "available_count": self.manager.get_available_slots(),
            "total_users": len(self.manager.users),
        }

    async def async_config_entry_first_refresh(self) -> None:
        """Perform first refresh and setup."""
        # Get configured locks from config entry (source of truth)
        configured_locks = self.config_entry.data.get(CONF_LOCKS, [])

        # Load saved user state (but not locks - config entry is source of truth for locks)
        stored = await self._store.async_load()
        if stored:
            # Only load user data, not lock list
            stored_users = stored.get("users", {})
            if stored_users:
                self.manager.load_state({"locks": [], "users": stored_users})

        # Add only locks from config entry
        self.manager.locks.clear()  # Clear any locks that might have been loaded
        for lock_name in configured_locks:
            self.manager.add_lock(lock_name)

        # Subscribe to MQTT topics
        await self._setup_mqtt_subscriptions()

        # Fetch initial data from locks
        try:
            await self.manager.fetch_users()
        except Exception as err:
            _LOGGER.warning("Failed to fetch users on startup: %s", err)

        # Set initial data
        self.async_set_updated_data(self._get_data())

    async def _setup_mqtt_subscriptions(self) -> None:
        """Set up MQTT subscriptions."""
        # Subscribe to lock status updates
        for lock_name in self.manager.locks:
            # Main topic for user data
            unsub = await mqtt.async_subscribe(
                self.hass,
                f"zigbee2mqtt/{lock_name}",
                self._handle_lock_message,
            )
            self._unsubscribe_mqtt.append(unsub)

            # Action topic for pin changes
            unsub = await mqtt.async_subscribe(
                self.hass,
                f"zigbee2mqtt/{lock_name}/action",
                self._handle_action_message,
            )
            self._unsubscribe_mqtt.append(unsub)

        _LOGGER.debug("Subscribed to MQTT topics for %d locks", len(self.manager.locks))

    @callback
    def _handle_lock_message(self, msg: mqtt.ReceiveMessage) -> None:
        """Handle lock MQTT message."""
        topic = msg.topic
        device = topic.split("/")[1] if "/" in topic else topic

        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError):
            return

        # Check for lock event fields (action, action_user, action_source_name)
        self._collect_lock_event(device, payload)

        self.hass.async_create_task(
            self.manager.process_lock_callback(device, payload)
        )

    @callback
    def _handle_action_message(self, msg: mqtt.ReceiveMessage) -> None:
        """Handle action MQTT message."""
        topic = msg.topic
        parts = topic.split("/")
        device = parts[1] if len(parts) > 1 else ""

        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode()

        self.hass.async_create_task(
            self._process_action(device, payload)
        )

    async def _process_action(self, device: str, payload: str) -> None:
        """Process action and send notification if needed."""
        notification = await self.manager.process_action_callback(device, payload)
        if notification:
            _LOGGER.info("LockMaster: %s", notification)
            # Could fire an event here for automations
            self.hass.bus.async_fire(
                f"{DOMAIN}_notification",
                {"message": notification, "device": device},
            )

    def _collect_lock_event(self, device: str, payload: dict) -> None:
        """Collect lock event data and fire event when complete."""
        action = payload.get(LOCK_EVENT_ACTION)
        user = payload.get(LOCK_EVENT_USER)
        source = payload.get(LOCK_EVENT_SOURCE)

        # Only process if we have at least one event field
        if not any([action, user, source]):
            return

        # Get or create pending event for this device
        if device not in self._pending_lock_events:
            self._pending_lock_events[device] = PendingLockEvent()

        pending = self._pending_lock_events[device]

        # Update with new data (non-None values)
        if action:
            pending.action = action
        if user is not None:
            # Convert master user code to friendly name
            pending.user = "Master" if str(user) == MASTER_USER_CODE else str(user)
        if source:
            pending.source = source

        pending.timestamp = datetime.now()

        # Cancel existing timer if any
        if pending.timer_task and not pending.timer_task.done():
            pending.timer_task.cancel()

        # Start debounce timer to fire event
        pending.timer_task = self.hass.async_create_task(
            self._debounce_fire_lock_event(device)
        )

    async def _debounce_fire_lock_event(self, device: str) -> None:
        """Wait for debounce period then fire lock event."""
        await asyncio.sleep(LOCK_EVENT_DEBOUNCE_SECONDS)
        await self._fire_lock_event(device)

    async def _fire_lock_event(self, device: str) -> None:
        """Fire the collected lock event."""
        if device not in self._pending_lock_events:
            return

        pending = self._pending_lock_events[device]

        # Only fire if we have at least action data
        if not pending.action:
            return

        # Look up user name from lockmaster users if we have a numeric user ID
        user_name = pending.user
        if user_name and user_name.isdigit():
            lm_user = self.manager.get_user(user_name)
            if lm_user and lm_user.name:
                user_name = lm_user.name

        # Suppress identical repeats within LOCK_EVENT_DEDUP_SECONDS. Handles
        # stuck-lock retransmit loops (same action+user+source every ~30s)
        # without losing legitimate repeated actions on a human cadence.
        event_key = (pending.action, user_name, pending.source)
        now = datetime.now()
        last = self._last_fired_events.get(device)
        if last is not None:
            last_key, last_time = last
            if (
                last_key == event_key
                and (now - last_time).total_seconds() < LOCK_EVENT_DEDUP_SECONDS
            ):
                _LOGGER.debug(
                    "Suppressing duplicate lock event for %s: %s",
                    device,
                    event_key,
                )
                del self._pending_lock_events[device]
                return

        self._last_fired_events[device] = (event_key, now)

        event_data = {
            "device": device,
            "action": pending.action,
            "user": user_name,
            "source": pending.source,
            "timestamp": pending.timestamp.isoformat(),
        }

        _LOGGER.debug("Firing lock event: %s", event_data)

        self.hass.bus.async_fire(f"{DOMAIN}_lock_event", event_data)

        # Clear pending event
        del self._pending_lock_events[device]

    async def _save_state(self) -> None:
        """Save state to storage."""
        await self._store.async_save(self.manager.save_state())

    async def async_shutdown(self) -> None:
        """Shutdown coordinator."""
        # Cancel any pending lock event timers
        for pending in self._pending_lock_events.values():
            if pending.timer_task and not pending.timer_task.done():
                pending.timer_task.cancel()
        self._pending_lock_events.clear()

        # Unsubscribe from MQTT
        for unsub in self._unsubscribe_mqtt:
            unsub()
        self._unsubscribe_mqtt.clear()

        # Save state
        await self._save_state()

    async def async_add_lock(self, lock_name: str) -> None:
        """Add a new lock."""
        self.manager.add_lock(lock_name)

        # Subscribe to new lock's MQTT topics
        unsub = await mqtt.async_subscribe(
            self.hass,
            f"zigbee2mqtt/{lock_name}",
            self._handle_lock_message,
        )
        self._unsubscribe_mqtt.append(unsub)

        unsub = await mqtt.async_subscribe(
            self.hass,
            f"zigbee2mqtt/{lock_name}/action",
            self._handle_action_message,
        )
        self._unsubscribe_mqtt.append(unsub)

        await self.manager.fetch_users()
        await self._save_state()

    async def async_remove_lock(self, lock_name: str) -> None:
        """Remove a lock."""
        self.manager.remove_lock(lock_name)
        await self._save_state()
        self.async_set_updated_data(self._get_data())
