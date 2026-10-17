"""Sensor platform for internet_radio_ma."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DEFAULT_NAME, DOMAIN
from .coordinator import InternetRadioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from config entry."""
    coordinator: InternetRadioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([InternetRadioDashboardSensor(coordinator, entry)])


class InternetRadioDashboardSensor(CoordinatorEntity[InternetRadioCoordinator], SensorEntity):
    """Main dashboard data sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: InternetRadioCoordinator, entry: ConfigEntry) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_dashboard"
        self._attr_object_id = "internet_radio_dashboard"
        self._attr_name = DEFAULT_NAME
        self._attr_icon = "mdi:radio"

    @property
    def native_value(self) -> str:
        """Return brief state string."""
        if not self.coordinator.data:
            return "unknown"
        current_station = self.coordinator.data.get("current_station", {})
        return current_station.get("name") or "idle"

    @property
    def extra_state_attributes(self) -> dict:
        """Expose rich dashboard data."""
        return self.coordinator.data or {}

    @property
    def device_info(self) -> DeviceInfo:
        """Device metadata for entity registry."""
        return DeviceInfo(
            identifiers={(DOMAIN, "dashboard")},
            name="Internet Radio MA",
            manufacturer="Custom",
            model="Dashboard Integration",
        )
