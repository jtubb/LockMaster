"""Sensor platform for LockMaster."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATUS_ENABLED
from .coordinator import LockMasterCoordinator


@dataclass(frozen=True, kw_only=True)
class LockMasterSensorEntityDescription(SensorEntityDescription):
    """Describes LockMaster sensor entity."""

    value_fn: Callable[[dict], Any]
    attr_fn: Callable[[dict], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[LockMasterSensorEntityDescription, ...] = (
    LockMasterSensorEntityDescription(
        key="enabled_users",
        translation_key="enabled_users",
        name="Enabled Users",
        icon="mdi:account-check",
        value_fn=lambda data: data.get("enabled_count", 0),
    ),
    LockMasterSensorEntityDescription(
        key="available_slots",
        translation_key="available_slots",
        name="Available Slots",
        icon="mdi:account-plus",
        value_fn=lambda data: data.get("available_count", 0),
    ),
    LockMasterSensorEntityDescription(
        key="total_users",
        translation_key="total_users",
        name="Total Users",
        icon="mdi:account-group",
        value_fn=lambda data: data.get("total_users", 0),
        attr_fn=lambda data: {
            "users": [
                {
                    "id": uid,
                    "name": u.get("name"),
                    "status": u.get("status"),
                    "email": u.get("email"),
                }
                for uid, u in data.get("users", {}).items()
            ]
        },
    ),
    LockMasterSensorEntityDescription(
        key="managed_locks",
        translation_key="managed_locks",
        name="Managed Locks",
        icon="mdi:lock",
        value_fn=lambda data: len(data.get("locks", [])),
        attr_fn=lambda data: {
            "locks": data.get("locks", []),
            "lock_slots": data.get("lock_slots", {}),
            "max_slots": data.get("max_slots", 0),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LockMaster sensors based on a config entry."""
    coordinator: LockMasterCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    # Add summary sensors
    for description in SENSOR_DESCRIPTIONS:
        entities.append(LockMasterSensor(coordinator, description))

    # Add individual user sensors for enabled users
    entities.append(LockMasterUserListSensor(coordinator))

    async_add_entities(entities)


class LockMasterSensor(CoordinatorEntity[LockMasterCoordinator], SensorEntity):
    """Representation of a LockMaster sensor."""

    entity_description: LockMasterSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LockMasterCoordinator,
        description: LockMasterSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.config_entry.entry_id)},
            "name": "LockMaster",
            "manufacturer": "LockMaster",
            "model": "Lock Manager",
            "entry_type": "service",
        }

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data:
            return self.entity_description.value_fn(self.coordinator.data)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        if self.coordinator.data and self.entity_description.attr_fn:
            return self.entity_description.attr_fn(self.coordinator.data)
        return None


class LockMasterUserListSensor(CoordinatorEntity[LockMasterCoordinator], SensorEntity):
    """Sensor showing all users with their details."""

    _attr_has_entity_name = True
    _attr_name = "User List"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, coordinator: LockMasterCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_user_list"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.config_entry.entry_id)},
            "name": "LockMaster",
            "manufacturer": "LockMaster",
            "model": "Lock Manager",
            "entry_type": "service",
        }

    @property
    def native_value(self) -> int:
        """Return number of enabled users."""
        if self.coordinator.data:
            return self.coordinator.data.get("enabled_count", 0)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all user details as attributes."""
        if not self.coordinator.data:
            return {}

        users = self.coordinator.data.get("users", {})
        enabled_users = []
        available_slots = []

        for uid, user in sorted(users.items(), key=lambda x: int(x[0])):
            user_info = {
                "id": uid,
                "name": user.get("name"),
                "email": user.get("email"),
                "pin": user.get("pin"),
                "status": user.get("status"),
                "last_update": user.get("last_update"),
            }

            if user.get("status") == STATUS_ENABLED:
                enabled_users.append(user_info)
            else:
                available_slots.append(user_info)

        return {
            "enabled_users": enabled_users,
            "available_slots": available_slots,
            "locks": self.coordinator.data.get("locks", []),
        }
