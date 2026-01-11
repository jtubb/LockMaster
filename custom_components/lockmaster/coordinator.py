"""DataUpdateCoordinator for LockMaster."""

from __future__ import annotations

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

    async def _save_state(self) -> None:
        """Save state to storage."""
        await self._store.async_save(self.manager.save_state())

    async def async_shutdown(self) -> None:
        """Shutdown coordinator."""
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
