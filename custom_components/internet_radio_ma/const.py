"""Constants for internet_radio_ma integration."""

from __future__ import annotations

DOMAIN = "internet_radio_ma"
DATA_COORDINATOR = "coordinator"
DATA_TRACK_HISTORY = "track_history"
DATA_LAST_TRACK = "last_track"

CONF_TARGET_PLAYER = "target_player"
CONF_DEVICE_IMAGE_ON = "device_image_on"
CONF_DEVICE_IMAGE_OFF = "device_image_off"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_PAGE_SIZE = "page_size"

DEFAULT_NAME = "Internet Radio Dashboard"
DEFAULT_UPDATE_INTERVAL = 15
DEFAULT_PAGE_SIZE = 9
MAX_HISTORY_ITEMS = 10
MAX_FAVORITES_PER_PAGE = 9

ATTR_FAVORITES = "favorites"
ATTR_CURRENT_STATION = "current_station"
ATTR_CURRENT_TRACK = "current_track"
ATTR_TRACK_HISTORY = "track_history"
ATTR_DEVICE_STATE = "device_state"
ATTR_DEVICE_ENTITY = "device_entity"
ATTR_DEVICE_IMAGE_ON = "device_image_on"
ATTR_DEVICE_IMAGE_OFF = "device_image_off"
ATTR_UPDATED = "updated"

SERVICE_PLAY_FAVORITE = "play_favorite"
SERVICE_REFRESH = "refresh"

SERVICE_ATTR_ENTITY_ID = "entity_id"
SERVICE_ATTR_FAVORITE_ID = "favorite_id"
SERVICE_ATTR_FAVORITE_INDEX = "favorite_index"
