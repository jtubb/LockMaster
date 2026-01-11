"""Config flow for LockMaster integration."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_LOCKS, CONF_LOCK_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_get_zigbee_locks(hass) -> list[str]:
    """Fetch available locks from zigbee2mqtt."""
    locks: list[str] = []
    event = asyncio.Event()

    @callback
    def message_received(msg):
        """Handle received MQTT message."""
        nonlocal locks
        try:
            devices = json.loads(msg.payload)
            for device in devices:
                # Check if device is a lock (has lock feature)
                if device.get("definition"):
                    exposes = device["definition"].get("exposes", [])
                    for expose in exposes:
                        # Check for lock feature
                        if expose.get("type") == "lock" or expose.get("property") == "child_lock":
                            locks.append(device.get("friendly_name", device.get("ieee_address")))
                            break
                        # Also check for features array (composite devices)
                        features = expose.get("features", [])
                        for feature in features:
                            if feature.get("type") == "lock" or feature.get("name") == "pin_code":
                                locks.append(device.get("friendly_name", device.get("ieee_address")))
                                break
        except (json.JSONDecodeError, TypeError, KeyError) as err:
            _LOGGER.debug("Error parsing zigbee2mqtt devices: %s", err)
        event.set()

    # Subscribe and wait for message
    unsub = await mqtt.async_subscribe(
        hass, "zigbee2mqtt/bridge/devices", message_received, qos=0
    )

    try:
        # Wait for response with timeout
        async with asyncio.timeout(5):
            await event.wait()
    except asyncio.TimeoutError:
        _LOGGER.warning("Timeout waiting for zigbee2mqtt device list")
    finally:
        unsub()

    return sorted(set(locks))


class LockMasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LockMaster."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._available_locks: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        # Fetch available locks if not already done
        if not self._available_locks:
            self._available_locks = await async_get_zigbee_locks(self.hass)

        if user_input is not None:
            # Check if already configured
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            # Handle both multi-select (CONF_LOCKS) and single text input (CONF_LOCK_NAME)
            if CONF_LOCKS in user_input:
                locks = user_input[CONF_LOCKS]
            else:
                locks = [user_input[CONF_LOCK_NAME].strip()]

            if not locks:
                errors["base"] = "invalid_lock_name"
            else:
                return self.async_create_entry(
                    title="LockMaster",
                    data={CONF_LOCKS: locks},
                )

        # Build schema based on available locks
        if self._available_locks:
            schema = vol.Schema({
                vol.Required(CONF_LOCKS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._available_locks,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=True,
                    )
                ),
            })
        else:
            # Fallback to text input if no locks found
            schema = vol.Schema({
                vol.Required(CONF_LOCK_NAME): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "lock_count": str(len(self._available_locks)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return LockMasterOptionsFlow(config_entry)


class LockMasterOptionsFlow(OptionsFlow):
    """Handle LockMaster options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._available_locks: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_lock", "remove_lock"],
        )

    async def async_step_add_lock(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add locks."""
        errors: dict[str, str] = {}
        current_locks = list(self.config_entry.data.get(CONF_LOCKS, []))

        # Fetch available locks if not already done
        if not self._available_locks:
            all_locks = await async_get_zigbee_locks(self.hass)
            # Filter out already configured locks
            self._available_locks = [l for l in all_locks if l not in current_locks]

        if user_input is not None:
            # Handle multi-select
            new_locks = user_input.get(CONF_LOCKS, [])

            if not new_locks:
                errors["base"] = "invalid_lock_name"
            else:
                current_locks.extend(new_locks)
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_LOCKS: current_locks},
                )
                return self.async_create_entry(title="", data={})

        if not self._available_locks:
            return self.async_abort(reason="no_locks_available")

        return self.async_show_form(
            step_id="add_lock",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCKS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._available_locks,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=True,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_remove_lock(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a lock."""
        current_locks = list(self.config_entry.data.get(CONF_LOCKS, []))

        if not current_locks:
            return self.async_abort(reason="no_locks")

        if user_input is not None:
            lock_name = user_input[CONF_LOCK_NAME]
            if lock_name in current_locks:
                current_locks.remove(lock_name)
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_LOCKS: current_locks},
                )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="remove_lock",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCK_NAME): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=current_locks,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )
