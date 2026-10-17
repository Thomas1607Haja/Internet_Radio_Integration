"""Internet Radio MA integration."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_ATTR_ENTITY_ID,
    SERVICE_ATTR_FAVORITE_ID,
    SERVICE_ATTR_FAVORITE_INDEX,
    SERVICE_PLAY_FAVORITE,
    SERVICE_REFRESH,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]
SERVICE_PLAY_FAVORITE_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(SERVICE_ATTR_FAVORITE_ID): cv.string,
        vol.Optional(SERVICE_ATTR_FAVORITE_INDEX): vol.Any(cv.positive_int, cv.string),
        vol.Optional("config_entry_id"): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration from YAML."""
    _ = config
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up internet_radio_ma from a config entry."""
    from .coordinator import InternetRadioCoordinator

    coordinator = InternetRadioCoordinator(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    card_path = Path(__file__).parent / "www" / "internet-radio-quad-card.js"
    await _async_publish_card_to_local_www(hass, card_path)

    # Store coordinator early so entities/services can still load even if first refresh fails.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_COORDINATOR: coordinator}

    # Do not block setup on first refresh; keep integration available and retry via coordinator.
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        _LOGGER.warning(
            "Initial data refresh failed for %s; integration stays loaded and will retry",
            DOMAIN,
        )

    async def _handle_play_favorite(call):
        target_entity = call.data.get(SERVICE_ATTR_ENTITY_ID, coordinator.target_player)
        favorite_id = call.data.get(SERVICE_ATTR_FAVORITE_ID)
        favorite_index_raw = call.data.get(SERVICE_ATTR_FAVORITE_INDEX)
        favorite_index = None
        if favorite_index_raw is not None:
            try:
                favorite_index = int(favorite_index_raw)
            except (TypeError, ValueError):
                _LOGGER.warning("Invalid favorite_index in service call: %s", favorite_index_raw)
        await coordinator.async_play_favorite(
            target_entity=target_entity,
            favorite_id=favorite_id,
            favorite_index=favorite_index,
        )

    async def _handle_refresh(call):
        _ = call
        await _async_publish_card_to_local_www(hass, card_path)
        await coordinator.async_request_refresh()

    # Re-register services on every setup so updated schemas are applied after reloads.
    if hass.services.has_service(DOMAIN, SERVICE_PLAY_FAVORITE):
        hass.services.async_remove(DOMAIN, SERVICE_PLAY_FAVORITE)
    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY_FAVORITE,
        _handle_play_favorite,
        schema=SERVICE_PLAY_FAVORITE_SCHEMA,
    )

    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_publish_card_to_local_www(hass: HomeAssistant, source_card_path: Path) -> None:
    """Publish the dashboard card JS to /config/www for stable /local serving."""
    target_path = Path(hass.config.path("www", "internet_radio_ma", "internet-radio-quad-card.js"))
    target_path.parent.mkdir(parents=True, exist_ok=True)

    def _copy_if_needed() -> None:
        if not source_card_path.exists():
            raise FileNotFoundError(f"Card source file not found: {source_card_path}")
        # Always overwrite to keep card in sync with integration updates.
        shutil.copyfile(source_card_path, target_path)

    try:
        await hass.async_add_executor_job(_copy_if_needed)
    except Exception as err:
        _LOGGER.warning("Could not publish dashboard card to /local: %s", err)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, SERVICE_PLAY_FAVORITE):
            hass.services.async_remove(DOMAIN, SERVICE_PLAY_FAVORITE)
        if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH)

    return unload_ok
