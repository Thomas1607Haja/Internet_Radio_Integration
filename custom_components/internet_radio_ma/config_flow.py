"""Config flow for internet_radio_ma."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_IMAGE_OFF,
    CONF_DEVICE_IMAGE_ON,
    CONF_PAGE_SIZE,
    CONF_TARGET_PLAYER,
    CONF_UPDATE_INTERVAL,
    DEFAULT_PAGE_SIZE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)


class InternetRadioMAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for internet_radio_ma."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Create the options flow."""
        return InternetRadioMAOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle first step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_TARGET_PLAYER])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Internet Radio ({user_input[CONF_TARGET_PLAYER]})",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_TARGET_PLAYER): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="media_player")
                ),
                vol.Optional(CONF_DEVICE_IMAGE_ON, default=""): str,
                vol.Optional(CONF_DEVICE_IMAGE_OFF, default=""): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=DEFAULT_UPDATE_INTERVAL,
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                vol.Optional(
                    CONF_PAGE_SIZE,
                    default=DEFAULT_PAGE_SIZE,
                ): vol.All(vol.Coerce(int), vol.Range(min=3, max=9)),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class InternetRadioMAOptionsFlow(config_entries.OptionsFlow):
    """Options flow for internet_radio_ma."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TARGET_PLAYER,
                    default=current.get(CONF_TARGET_PLAYER, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="media_player")
                ),
                vol.Optional(
                    CONF_DEVICE_IMAGE_ON,
                    default=current.get(CONF_DEVICE_IMAGE_ON, ""),
                ): str,
                vol.Optional(
                    CONF_DEVICE_IMAGE_OFF,
                    default=current.get(CONF_DEVICE_IMAGE_OFF, ""),
                ): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                vol.Optional(
                    CONF_PAGE_SIZE,
                    default=current.get(CONF_PAGE_SIZE, DEFAULT_PAGE_SIZE),
                ): vol.All(vol.Coerce(int), vol.Range(min=3, max=9)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
