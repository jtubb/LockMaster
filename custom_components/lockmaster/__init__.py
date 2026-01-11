"""The LockMaster integration."""

from __future__ import annotations

from datetime import datetime
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_USER_EMAIL,
    ATTR_USER_ID,
    ATTR_USER_NAME,
    ATTR_USER_PIN,
    ATTR_RESERVATION_END,
    ATTR_RESERVATION_START,
    CONF_LOCK_NAME,
    DOMAIN,
)
from .coordinator import LockMasterCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Service schemas
SERVICE_ADD_LOCK = "add_lock"
SERVICE_ADD_LOCK_SCHEMA = vol.Schema({
    vol.Required(CONF_LOCK_NAME): cv.string,
})

SERVICE_FETCH_USERS = "fetch_users"
SERVICE_REFRESH_USERS = "refresh_users"

SERVICE_UPDATE_USER = "update_user"
SERVICE_UPDATE_USER_SCHEMA = vol.Schema({
    vol.Required(ATTR_USER_ID): cv.string,
    vol.Required(ATTR_USER_NAME): cv.string,
    vol.Required(ATTR_USER_EMAIL): cv.string,
    vol.Required(ATTR_USER_PIN): cv.string,
})

SERVICE_DISABLE_USER = "disable_user"
SERVICE_DISABLE_USER_SCHEMA = vol.Schema({
    vol.Required(ATTR_USER_ID): cv.string,
})

SERVICE_GENERATE_TEMP_USER = "generate_temp_user"
SERVICE_GENERATE_TEMP_USER_SCHEMA = vol.Schema({
    vol.Required(ATTR_USER_NAME): cv.string,
    vol.Required(ATTR_USER_EMAIL): cv.string,
    vol.Required(ATTR_RESERVATION_START): cv.datetime,
    vol.Required(ATTR_RESERVATION_END): cv.datetime,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LockMaster from a config entry."""
    coordinator = LockMasterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _async_register_services(hass)

    # Listen for config updates
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register LockMaster services."""

    def get_coordinator() -> LockMasterCoordinator | None:
        """Get the coordinator."""
        for coordinator in hass.data.get(DOMAIN, {}).values():
            if isinstance(coordinator, LockMasterCoordinator):
                return coordinator
        return None

    async def handle_add_lock(call: ServiceCall) -> None:
        """Handle add_lock service."""
        coordinator = get_coordinator()
        if coordinator:
            await coordinator.async_add_lock(call.data[CONF_LOCK_NAME])

    async def handle_fetch_users(call: ServiceCall) -> None:
        """Handle fetch_users service."""
        coordinator = get_coordinator()
        if coordinator:
            await coordinator.manager.fetch_users()

    async def handle_refresh_users(call: ServiceCall) -> None:
        """Handle refresh_users service."""
        coordinator = get_coordinator()
        if coordinator:
            await coordinator.manager.consistency_check()

    async def handle_update_user(call: ServiceCall) -> ServiceResponse:
        """Handle update_user service."""
        coordinator = get_coordinator()
        if not coordinator:
            return {"success": False, "status": "LockMaster not configured"}

        result = await coordinator.manager.update_user(
            call.data[ATTR_USER_ID],
            call.data[ATTR_USER_NAME],
            call.data[ATTR_USER_EMAIL],
            call.data[ATTR_USER_PIN],
        )
        return result

    async def handle_disable_user(call: ServiceCall) -> ServiceResponse:
        """Handle disable_user service."""
        coordinator = get_coordinator()
        if not coordinator:
            return {"success": False, "status": "LockMaster not configured"}

        result = await coordinator.manager.disable_user(call.data[ATTR_USER_ID])
        return result

    async def handle_generate_temp_user(call: ServiceCall) -> ServiceResponse:
        """Handle generate_temp_user service."""
        coordinator = get_coordinator()
        if not coordinator:
            return {"success": False, "status": "LockMaster not configured"}

        start = call.data[ATTR_RESERVATION_START]
        end = call.data[ATTR_RESERVATION_END]

        # Convert to datetime if needed
        if not isinstance(start, datetime):
            start = datetime.fromisoformat(str(start))
        if not isinstance(end, datetime):
            end = datetime.fromisoformat(str(end))

        result = await coordinator.manager.allocate_temp_user(
            call.data[ATTR_USER_NAME],
            call.data[ATTR_USER_EMAIL],
            start,
            end,
        )
        return result

    # Register all services
    if not hass.services.has_service(DOMAIN, SERVICE_ADD_LOCK):
        hass.services.async_register(
            DOMAIN, SERVICE_ADD_LOCK, handle_add_lock, schema=SERVICE_ADD_LOCK_SCHEMA
        )

    if not hass.services.has_service(DOMAIN, SERVICE_FETCH_USERS):
        hass.services.async_register(DOMAIN, SERVICE_FETCH_USERS, handle_fetch_users)

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_USERS):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_USERS, handle_refresh_users)

    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_USER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_USER,
            handle_update_user,
            schema=SERVICE_UPDATE_USER_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DISABLE_USER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DISABLE_USER,
            handle_disable_user,
            schema=SERVICE_DISABLE_USER_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GENERATE_TEMP_USER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GENERATE_TEMP_USER,
            handle_generate_temp_user,
            schema=SERVICE_GENERATE_TEMP_USER_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: LockMasterCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

        # Remove services if no more entries
        if not hass.data[DOMAIN]:
            for service in [
                SERVICE_ADD_LOCK,
                SERVICE_FETCH_USERS,
                SERVICE_REFRESH_USERS,
                SERVICE_UPDATE_USER,
                SERVICE_DISABLE_USER,
                SERVICE_GENERATE_TEMP_USER,
            ]:
                hass.services.async_remove(DOMAIN, service)

    return unload_ok
