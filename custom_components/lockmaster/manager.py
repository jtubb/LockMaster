"""LockMaster manager for handling locks and users."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import random
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from pyisemail import is_email

from .const import (
    DEFAULT_EMAIL,
    DEFAULT_PIN,
    STATUS_AVAILABLE,
    STATUS_ENABLED,
)

_LOGGER = logging.getLogger(__name__)

# Timeout for waiting for lock confirmation (seconds)
LOCK_CONFIRMATION_TIMEOUT = 10


@dataclass
class LockUser:
    """Represents a user slot on a lock."""

    pin: str | None = None
    status: str = STATUS_AVAILABLE


@dataclass
class Lock:
    """Represents a managed lock."""

    name: str = ""
    users: dict[str, LockUser] = field(default_factory=dict)
    callback_count: int = 0
    synced: bool = False
    slot_count: int = 0  # Number of user slots this lock supports
    last_update: float = field(default_factory=lambda: datetime.utcnow().timestamp())


@dataclass
class LMUser:
    """Represents a LockMaster user."""

    user_id: str = ""
    name: str | None = None
    email: str = DEFAULT_EMAIL
    pin: str = DEFAULT_PIN
    status: str = STATUS_AVAILABLE
    last_update: float = field(default_factory=lambda: datetime.utcnow().timestamp())
    reservation_start: datetime | None = None
    reservation_end: datetime | None = None
    consistency_error: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "name": self.name,
            "email": self.email,
            "pin": self.pin,
            "status": self.status,
            "last_update": self.last_update,
            "reservationStart": self.reservation_start.isoformat() if self.reservation_start else None,
            "reservationEnd": self.reservation_end.isoformat() if self.reservation_end else None,
        }

    @classmethod
    def from_dict(cls, user_id: str, data: dict) -> "LMUser":
        """Create from dictionary."""
        user = cls(user_id=user_id)
        user.name = data.get("name") or f"User {int(user_id) + 1}"
        user.email = data.get("email") or DEFAULT_EMAIL
        user.pin = data.get("pin") or DEFAULT_PIN
        user.status = data.get("status") or STATUS_AVAILABLE
        user.last_update = data.get("last_update") or datetime.utcnow().timestamp()
        if data.get("reservationStart"):
            user.reservation_start = datetime.fromisoformat(data["reservationStart"])
        if data.get("reservationEnd"):
            user.reservation_end = datetime.fromisoformat(data["reservationEnd"])
        return user


class LockManagerError(Exception):
    """Base exception for LockManager errors."""


class LockManager:
    """Manages locks and users."""

    def __init__(self) -> None:
        """Initialize the lock manager."""
        self.users: dict[str, LMUser] = {}
        self.locks: dict[str, Lock] = {}
        self.last_update: float = datetime.utcnow().timestamp()
        self._mqtt_publish: Callable | None = None
        self._update_callback: Callable | None = None
        # Pending operations: {device_name: {operation_type: asyncio.Event}}
        # operation_type is "add" or "delete"
        self._pending_operations: dict[str, dict[str, asyncio.Event]] = {}

    @property
    def max_slots(self) -> int:
        """Get the maximum number of user slots (minimum across all synced locks)."""
        synced_locks = [lock for lock in self.locks.values() if lock.synced and lock.slot_count > 0]
        if not synced_locks:
            # Return current user count if no locks synced yet
            return len(self.users) if self.users else 0
        return min(lock.slot_count for lock in synced_locks)

    def set_mqtt_publish(self, publish_func: callable) -> None:
        """Set the MQTT publish function."""
        self._mqtt_publish = publish_func

    def set_update_callback(self, callback: callable) -> None:
        """Set callback for when data updates."""
        self._update_callback = callback

    async def _notify_update(self) -> None:
        """Notify that data has been updated."""
        if self._update_callback:
            await self._update_callback()

    async def _wait_for_lock_confirmations(
        self, operation_type: str, timeout: float = LOCK_CONFIRMATION_TIMEOUT
    ) -> dict[str, bool]:
        """Wait for all locks to confirm an operation.

        Events must be set up in _pending_operations before calling this method.

        Args:
            operation_type: "add" or "delete"
            timeout: Maximum seconds to wait for each lock

        Returns:
            Dict mapping lock name to success (True if confirmed, False if timed out)
        """
        results: dict[str, bool] = {}

        # Wait for all locks to confirm
        for device_name in self.locks:
            if device_name not in self._pending_operations:
                results[device_name] = False
                _LOGGER.warning("No pending operation for %s", device_name)
                continue

            if operation_type not in self._pending_operations[device_name]:
                results[device_name] = False
                _LOGGER.warning("No %s operation pending for %s", operation_type, device_name)
                continue

            event = self._pending_operations[device_name][operation_type]
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
                results[device_name] = True
                _LOGGER.debug("Lock %s confirmed %s", device_name, operation_type)
            except asyncio.TimeoutError:
                results[device_name] = False
                _LOGGER.warning(
                    "Lock %s did not confirm %s within %s seconds",
                    device_name, operation_type, timeout
                )

        # Clean up pending operations
        for device_name in list(self._pending_operations.keys()):
            if device_name in self._pending_operations:
                self._pending_operations[device_name].pop(operation_type, None)
                if not self._pending_operations[device_name]:
                    del self._pending_operations[device_name]

        return results

    def add_lock(self, lock_name: str) -> None:
        """Add a lock to manage."""
        if lock_name not in self.locks:
            self.locks[lock_name] = Lock(name=lock_name)
            _LOGGER.debug("Added lock: %s", lock_name)

    def remove_lock(self, lock_name: str) -> None:
        """Remove a lock."""
        if lock_name in self.locks:
            del self.locks[lock_name]
            _LOGGER.debug("Removed lock: %s", lock_name)

    def clear(self) -> None:
        """Clear all users and locks."""
        self.users = {}
        self.locks = {}

    def load_state(self, data: dict) -> None:
        """Load state from stored data."""
        _LOGGER.debug("Loading LockMaster state")
        for lock_name in data.get("locks", []):
            self.locks.setdefault(lock_name, Lock(name=lock_name))
        for user_id, user_data in data.get("users", {}).items():
            self.users[user_id] = LMUser.from_dict(user_id, user_data)
        _LOGGER.debug("Loaded %d locks and %d users", len(self.locks), len(self.users))

    def save_state(self) -> dict:
        """Save state to dictionary."""
        return {
            "locks": list(self.locks.keys()),
            "users": {uid: user.to_dict() for uid, user in self.users.items()},
        }

    async def process_lock_callback(self, device: str, payload: dict) -> None:
        """Process callback from a lock device."""
        if device not in self.locks:
            return

        _LOGGER.debug("Processing callback for %s", device)
        lock = self.locks[device]
        lock.callback_count += 1

        response = payload
        if "users" not in response:
            return

        # Set slot count from response
        lock.slot_count = len(response["users"])
        _LOGGER.debug("Lock %s has %d user slots", device, lock.slot_count)

        # Initialize users dict if needed
        if len(lock.users) != len(response["users"]):
            for i in range(len(response["users"])):
                lock.users.setdefault(str(i), LockUser())

        for user_id, user_data in response["users"].items():
            user_status = user_data.get("status", STATUS_AVAILABLE)
            lock.users[user_id].status = user_status

            pin_value = user_data.get("pin_code")
            if isinstance(pin_value, dict):
                if pin_value:
                    try:
                        sorted_keys = sorted([int(k) for k in pin_value.keys()])
                        pin_chars = [chr(pin_value[str(k)]) for k in sorted_keys]
                        pin_value = "".join(pin_chars)
                    except (ValueError, TypeError):
                        pin_value = pin_value.get("pin_code", 0)
                else:
                    pin_value = None

            # Only use the PIN if the lock reports the user as enabled
            # Lock firmware may report old PIN data even for available slots
            if pin_value and str(pin_value).isdigit() and user_status == STATUS_ENABLED:
                lock.users[user_id].pin = str(pin_value)
            else:
                lock.users[user_id].pin = None

        if lock.callback_count >= len(lock.users):
            lock.callback_count = 0
            lock.synced = True
            _LOGGER.debug("Lock %s synced", device)
            await self.consistency_check()

        self.last_update = datetime.utcnow().timestamp()

    async def process_action_callback(self, device: str, payload: str) -> str | None:
        """Process action callback (pin added/deleted)."""
        if device not in self.locks:
            return None

        if payload in ("pin_code_added", "pin_code_deleted"):
            self.last_update = datetime.utcnow().timestamp()

            # Signal any pending operations for this device
            operation_type = "add" if payload == "pin_code_added" else "delete"
            if device in self._pending_operations:
                if operation_type in self._pending_operations[device]:
                    self._pending_operations[device][operation_type].set()
                    _LOGGER.debug("Lock %s confirmed %s operation", device, operation_type)

            return f"Pin Code Changed: {payload}"
        return None

    async def fetch_users(self) -> None:
        """Fetch users from all locks."""
        if not self._mqtt_publish:
            raise LockManagerError("MQTT publish not configured")

        for device_id in self.locks:
            self.locks[device_id].synced = False
            _LOGGER.debug("Fetching users from %s", device_id)
            await self._mqtt_publish(
                f"zigbee2mqtt/{device_id}/get",
                json.dumps({"pin_code": ""}),
            )

    async def consistency_check(self) -> None:
        """Check consistency between locks and users."""
        max_slots = self.max_slots
        _LOGGER.debug("Running consistency check with max_slots=%d", max_slots)

        # Trim users to max_slots (remove users beyond the limit)
        if max_slots > 0:
            users_to_remove = [uid for uid in self.users if int(uid) >= max_slots]
            for uid in users_to_remove:
                del self.users[uid]
                _LOGGER.debug("Removed user slot %s (beyond max_slots)", uid)

        # First pass: collect PINs from all locks for each user slot
        slot_pins: dict[str, dict[str, str]] = {}  # user_id -> {lock_name: pin}
        for device, lock in self.locks.items():
            for user_id, lock_user in lock.users.items():
                if max_slots > 0 and int(user_id) >= max_slots:
                    continue
                self.users.setdefault(user_id, LMUser(user_id=user_id))
                slot_pins.setdefault(user_id, {})
                if lock_user.pin and lock_user.pin != DEFAULT_PIN:
                    slot_pins[user_id][device] = lock_user.pin

        # Second pass: check consistency and sync
        for user_id, lock_pins in slot_pins.items():
            user = self.users[user_id]

            # Set default name
            if user_id == "0":
                user.name = "Master"
            if user.name is None:
                user.name = f"User {int(user_id) + 1}"

            # Get unique PINs across all locks
            unique_pins = set(lock_pins.values())

            if len(unique_pins) == 0:
                # No locks have a PIN for this user
                user.status = STATUS_AVAILABLE
                user.pin = DEFAULT_PIN
                user.consistency_error = False
            elif len(unique_pins) == 1:
                # All locks agree on the PIN
                user.pin = unique_pins.pop()
                user.status = STATUS_ENABLED
                user.consistency_error = False
            else:
                # PIN mismatch between locks - need to resync
                _LOGGER.warning(
                    "PIN mismatch for %s across locks: %s - will resync",
                    user.name,
                    {k: v for k, v in lock_pins.items()}
                )
                user.consistency_error = True

                # Use the most common PIN, or the stored PIN if we have one
                if user.pin and user.pin != DEFAULT_PIN and user.pin in unique_pins:
                    correct_pin = user.pin
                else:
                    # Use most common PIN across locks
                    pin_counts = {}
                    for pin in lock_pins.values():
                        pin_counts[pin] = pin_counts.get(pin, 0) + 1
                    correct_pin = max(pin_counts, key=pin_counts.get)

                user.pin = correct_pin
                user.status = STATUS_ENABLED

                # Push correct PIN to out-of-sync locks
                for device, lock_pin in lock_pins.items():
                    if lock_pin != correct_pin:
                        _LOGGER.info("Resyncing %s on %s with correct PIN", user.name, device)
                        await self._push_pin_to_lock(device, user_id, correct_pin)

        await self._notify_update()

    async def _push_pin_to_lock(self, device: str, user_id: str, pin: str) -> None:
        """Push a PIN to a specific lock."""
        if not self._mqtt_publish:
            return

        await self._mqtt_publish(
            f"zigbee2mqtt/{device}/set",
            json.dumps({
                "pin_code": {
                    "user": user_id,
                    "user_type": "unrestricted",
                    "user_enabled": "true",
                    "pin_code": str(pin),
                }
            }),
        )

    async def update_user(
        self, user_id: str, name: str, email: str, pin: str
    ) -> dict:
        """Update a user."""
        response = {"success": False, "status": None}

        # Validate user_id is within max_slots
        max_slots = self.max_slots
        if max_slots > 0 and int(user_id) >= max_slots:
            response["status"] = f"User ID {user_id} exceeds available slots ({max_slots})."
            return response

        if not is_email(email):
            response["status"] = "Invalid email address."
            return response

        if int(user_id) != 0:
            self.users.setdefault(user_id, LMUser(user_id=user_id))
            self.users[user_id].name = name
        self.users[user_id].email = email

        pin_response = self._validate_pin(pin, user_id)
        if not pin_response["success"]:
            return pin_response

        if not self._mqtt_publish:
            response["status"] = "MQTT not configured"
            return response

        # Set up pending operations before publishing
        for device_id in self.locks:
            if device_id not in self._pending_operations:
                self._pending_operations[device_id] = {}
            self._pending_operations[device_id]["add"] = asyncio.Event()

        # Publish to all locks
        for device_id in self.locks:
            await self._mqtt_publish(
                f"zigbee2mqtt/{device_id}/set",
                json.dumps({
                    "pin_code": {
                        "user": user_id,
                        "user_type": "unrestricted",
                        "user_enabled": "true",
                        "pin_code": str(pin),
                    }
                }),
            )

        # Wait for lock confirmations
        confirmations = await self._wait_for_lock_confirmations("add")
        failed_locks = [name for name, confirmed in confirmations.items() if not confirmed]

        if failed_locks:
            response["success"] = False
            response["status"] = f"Timeout waiting for confirmation from: {', '.join(failed_locks)}"
            return response

        self.users[user_id].pin = pin
        self.users[user_id].status = STATUS_ENABLED
        self.users[user_id].last_update = datetime.utcnow().timestamp()

        await self._notify_update()

        response["success"] = True
        response["status"] = f"Updated user {self.users[user_id].name} (confirmed by all locks)"
        return response

    async def disable_user(self, user_id: str) -> dict:
        """Disable a user."""
        response = {"success": False, "status": None}

        if int(user_id) == 0:
            response["status"] = "Cannot disable Master user. Consider changing PIN instead."
            return response

        if user_id not in self.users:
            response["status"] = f"User {user_id} not found."
            return response

        original_name = self.users[user_id].name

        if not self._mqtt_publish:
            response["status"] = "MQTT not configured"
            return response

        # Set up pending operations before publishing
        for device_id in self.locks:
            if device_id not in self._pending_operations:
                self._pending_operations[device_id] = {}
            self._pending_operations[device_id]["delete"] = asyncio.Event()

        # Publish to all locks
        for device_id in self.locks:
            await self._mqtt_publish(
                f"zigbee2mqtt/{device_id}/set",
                json.dumps({
                    "pin_code": {
                        "user": user_id,
                        "pin_code": None,
                    }
                }),
            )

        # Wait for lock confirmations
        confirmations = await self._wait_for_lock_confirmations("delete")
        failed_locks = [name for name, confirmed in confirmations.items() if not confirmed]

        if failed_locks:
            response["success"] = False
            response["status"] = f"Timeout waiting for confirmation from: {', '.join(failed_locks)}"
            return response

        self.users[user_id].pin = DEFAULT_PIN
        self.users[user_id].status = STATUS_AVAILABLE
        self.users[user_id].email = DEFAULT_EMAIL
        self.users[user_id].name = f"User {int(user_id) + 1}"
        self.users[user_id].last_update = datetime.utcnow().timestamp()

        await self._notify_update()

        response["success"] = True
        response["status"] = f"Disabled user {original_name} (confirmed by all locks)"
        return response

    async def allocate_temp_user(
        self, name: str, email: str, start: datetime, end: datetime
    ) -> dict:
        """Allocate a temporary user."""
        response = {"success": False, "status": None}

        max_slots = self.max_slots
        if max_slots == 0:
            response["status"] = "No locks synced yet. Please wait for locks to sync."
            return response

        # Find available slot (search from end to preserve lower slots)
        for user_id in range(max_slots - 1, 1, -1):
            user_id_str = str(user_id)
            if self.users.get(user_id_str, LMUser()).status == STATUS_AVAILABLE:
                # Generate random PIN
                for _ in range(100):  # Max attempts
                    pin = str(random.randrange(1000, 99999999))
                    pin_response = self._validate_pin(pin, user_id_str)
                    if pin_response["success"]:
                        self.users.setdefault(user_id_str, LMUser(user_id=user_id_str))
                        self.users[user_id_str].reservation_start = start
                        self.users[user_id_str].reservation_end = end

                        user_response = await self.update_user(user_id_str, name, email, pin)
                        if user_response["success"]:
                            response["success"] = True
                            response["status"] = f"Added temp user {name}"
                            response["pin"] = pin
                            response["user_id"] = user_id_str
                            return response

        response["status"] = "No available door lock accounts."
        return response

    def _validate_pin(self, pin: str, exclude_user_id: str = None) -> dict:
        """Validate a PIN code."""
        response = {"success": False, "status": None}

        try:
            if int(pin) == 0:
                response["status"] = "Invalid PIN"
                return response
        except (ValueError, TypeError):
            response["status"] = "PIN must be numeric"
            return response

        # PIN must be 4-8 digits and not all same number
        if not re.fullmatch(r"^(\d)(?!\1+$)\d{3,8}$", str(pin)):
            response["status"] = "PIN must be 4-8 digits and not all the same number."
            return response

        # Check uniqueness
        for user_id, user in self.users.items():
            if user_id != exclude_user_id and user.pin == pin and user.status == STATUS_ENABLED:
                response["status"] = "PIN must be unique."
                return response

        response["success"] = True
        return response

    def get_user(self, user_id: str) -> LMUser | None:
        """Get a user by ID."""
        return self.users.get(user_id)

    def get_all_users(self) -> list[LMUser]:
        """Get all users."""
        return list(self.users.values())

    def get_enabled_users(self) -> list[LMUser]:
        """Get enabled users."""
        return [u for u in self.users.values() if u.status == STATUS_ENABLED]

    def get_available_slots(self) -> int:
        """Get number of available slots."""
        return sum(1 for u in self.users.values() if u.status == STATUS_AVAILABLE)
