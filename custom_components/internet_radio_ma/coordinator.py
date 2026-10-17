"""DataUpdateCoordinator for internet_radio_ma."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote_plus, urlparse
import re
import html as html_lib

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .const import (
    ATTR_CURRENT_STATION,
    ATTR_CURRENT_TRACK,
    ATTR_DEVICE_ENTITY,
    ATTR_DEVICE_IMAGE_OFF,
    ATTR_DEVICE_IMAGE_ON,
    ATTR_DEVICE_STATE,
    ATTR_FAVORITES,
    ATTR_TRACK_HISTORY,
    ATTR_UPDATED,
    CONF_DEVICE_IMAGE_OFF,
    CONF_DEVICE_IMAGE_ON,
    CONF_PAGE_SIZE,
    CONF_TARGET_PLAYER,
    CONF_UPDATE_INTERVAL,
    DEFAULT_PAGE_SIZE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_FAVORITES_PER_PAGE,
    MAX_HISTORY_ITEMS,
)
from .helpers import normalize_track_key, utc_iso_now

_LOGGER = logging.getLogger(__name__)
BACKFILL_INTERVAL_SECONDS = 60
MAX_PLAYBACK_WINDOWS = 120
BACKFILL_WINDOW_LOOKBACK = timedelta(minutes=12)
DUPLICATE_TIME_WINDOW_SECONDS = 15 * 60


class InternetRadioCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate internet radio state across MA and media player."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}
        self.target_player: str = cfg[CONF_TARGET_PLAYER]
        self.page_size = min(
            cfg.get(CONF_PAGE_SIZE, DEFAULT_PAGE_SIZE), MAX_FAVORITES_PER_PAGE
        )
        self.device_image_on = cfg.get(CONF_DEVICE_IMAGE_ON, "")
        self.device_image_off = cfg.get(CONF_DEVICE_IMAGE_OFF, "")
        self._global_history: deque[dict[str, str]] = deque(maxlen=MAX_HISTORY_ITEMS)
        self._last_global_track_key: str = ""
        self._station_logo_cache: dict[str, str] = {}
        self._last_station_name: str = ""
        self._last_backfill_check: datetime | None = None
        self._playback_windows: deque[dict[str, Any]] = deque(maxlen=MAX_PLAYBACK_WINDOWS)
        self._active_playback_window: dict[str, Any] | None = None
        self._ma_config_entry_id = self._resolve_ma_config_entry_id()

        interval = cfg.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest data for sensor attributes."""
        try:
            favorites = await self._async_get_radio_favorites()
            player_snapshot = await self._async_get_player_snapshot(self.target_player)

            station = player_snapshot.get("station_name", "")
            track = player_snapshot.get("track_title", "")
            artist = player_snapshot.get("track_artist", "")
            now_utc = dt_util.utcnow()
            self._track_playback_window(player_snapshot, now_utc)
            if track and not self._looks_like_station_label(track):
                self._append_history(
                    station,
                    track,
                    artist,
                    player_snapshot.get("station_logo", ""),
                    player_snapshot.get("track_logo", ""),
                )

            if self._should_run_backfill(now_utc):
                await self._async_backfill_history_for_station(
                    station_name=station,
                    station_logo=player_snapshot.get("station_logo", ""),
                    now_utc=now_utc,
                )
                self._last_backfill_check = now_utc

            history = list(self._global_history)

            return {
                ATTR_FAVORITES: favorites,
                ATTR_CURRENT_STATION: {
                    "name": station,
                    "logo": player_snapshot.get("station_logo", ""),
                },
                ATTR_CURRENT_TRACK: {
                    "title": track,
                    "artist": artist,
                    "logo": player_snapshot.get("track_logo", ""),
                },
                ATTR_TRACK_HISTORY: list(reversed(history)),
                ATTR_DEVICE_STATE: player_snapshot.get("state", STATE_UNKNOWN),
                ATTR_DEVICE_ENTITY: self.target_player,
                ATTR_DEVICE_IMAGE_ON: self.device_image_on,
                ATTR_DEVICE_IMAGE_OFF: self.device_image_off,
                "debug_player_attrs": player_snapshot.get("debug_attrs", {}),
                "debug_queue_lookup": player_snapshot.get("debug_queue", {}),
                ATTR_UPDATED: utc_iso_now(),
            }
        except Exception as err:  # pragma: no cover
            raise UpdateFailed(f"Failed to update internet radio data: {err}") from err

    async def async_play_favorite(
        self,
        target_entity: str,
        favorite_id: str | None = None,
        favorite_index: int | None = None,
    ) -> None:
        """Play selected favorite on the target media player."""
        favorites = self.data.get(ATTR_FAVORITES, []) if self.data else []

        selected: dict[str, Any] | None = None
        if favorite_id:
            selected = next((item for item in favorites if item.get("id") == favorite_id), None)
        elif favorite_index is not None and 0 <= favorite_index < len(favorites):
            selected = favorites[favorite_index]

        if not selected:
            _LOGGER.warning("No favorite selected for playback")
            return

        selected_name = selected.get("name")
        if isinstance(selected_name, str) and selected_name.strip():
            self._last_station_name = selected_name.strip()

        media_id = selected.get("uri") or selected.get("stream_url") or selected.get("name")
        if not media_id:
            _LOGGER.warning("Selected favorite does not contain playable media id")
            return

        if self.hass.services.has_service("music_assistant", "play_media"):
            service_data = {
                "media_id": media_id,
                "media_type": "radio",
            }
            await self._async_call_music_assistant(
                "play_media",
                service_data,
                target={"entity_id": target_entity},
                blocking=True,
            )
            return

        # Fallback for setups where MA playback service is not available.
        await self.hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": target_entity,
                "media_content_id": media_id,
                "media_content_type": "music",
            },
            blocking=True,
        )

    async def _async_get_radio_favorites(self) -> list[dict[str, Any]]:
        """Read radio favorites from Music Assistant service API."""
        if not self.hass.services.has_service("music_assistant", "get_library"):
            _LOGGER.warning("music_assistant.get_library service not available")
            return []

        service_data: dict[str, Any] = {
            "media_type": "radio",
            "favorite": True,
            "limit": 500,
            "order_by": "name",
        }

        try:
            response = await self._async_call_music_assistant(
                "get_library",
                service_data,
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not read MA favorites: %s", err)
            return []

        items = response.get("items", []) if isinstance(response, dict) else []
        return await self._async_normalize_favorites(items)

    async def _async_call_music_assistant(
        self,
        service: str,
        service_data: dict[str, Any],
        *,
        target: dict[str, Any] | None = None,
        blocking: bool = False,
        return_response: bool = False,
    ) -> Any:
        """Call a Music Assistant service with backwards-compatible payload."""
        payload = dict(service_data)
        if self._ma_config_entry_id:
            payload["config_entry_id"] = self._ma_config_entry_id

        try:
            return await self.hass.services.async_call(
                "music_assistant",
                service,
                payload,
                target=target,
                blocking=blocking,
                return_response=return_response,
            )
        except Exception as err:  # noqa: BLE001 - HA may surface validation errors as non-HomeAssistantError
            if not self._ma_config_entry_id or "config_entry_id" not in str(err):
                raise

            _LOGGER.debug(
                "music_assistant.%s does not accept config_entry_id, retrying without it: %s",
                service,
                err,
            )
            return await self.hass.services.async_call(
                "music_assistant",
                service,
                service_data,
                target=target,
                blocking=blocking,
                return_response=return_response,
            )

    async def _async_get_player_snapshot(self, entity_id: str) -> dict[str, Any]:
        """Collect player state and augment with external metadata when needed."""
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Target player entity not found: %s", entity_id)
            return {
                "state": STATE_UNKNOWN,
                "station_name": "",
                "track_title": "",
                "track_artist": "",
                "station_logo": "",
                "track_logo": "",
            }

        if state.state == "off":
            return {
                "state": state.state,
                "station_name": "",
                "track_title": "",
                "track_artist": "",
                "station_logo": "",
                "track_logo": "",
            }

        attrs = state.attributes
        station_name = self._extract_station_name(attrs)
        track_title, track_artist = self._extract_track_metadata(attrs)
        station_logo = attrs.get("entity_picture") or ""
        track_logo = ""
        debug_attrs = self._build_debug_attrs(attrs)
        queue_track: dict[str, Any] = {}

        if not track_title or self._looks_like_station_label(track_title):
            queue_track = await self._async_get_queue_track_metadata(entity_id, attrs)
            if queue_track.get("title"):
                track_title = queue_track.get("title", "")
            if queue_track.get("artist"):
                track_artist = queue_track.get("artist", "")
            if queue_track.get("logo"):
                track_logo = queue_track.get("logo", "")

        if (not track_title or self._looks_like_station_label(track_title)) and station_name:
            web_track = await self._async_lookup_track_from_radio_playlists(station_name)
            if web_track.get("title"):
                track_title = web_track.get("title", "")
            if web_track.get("artist"):
                track_artist = web_track.get("artist", "")
            if web_track.get("logo"):
                track_logo = web_track.get("logo", "")

        if self._is_track_like_station(station_name, track_title, track_artist):
            track_logo = ""

        if not station_logo and station_name:
            station_logo = await self._async_lookup_station_logo(station_name)

        if not track_logo and track_title and not self._looks_like_station_label(track_title):
            track_logo = await self._async_lookup_track_logo(track_title, track_artist)

        return {
            "state": state.state,
            "station_name": station_name,
            "track_title": track_title,
            "track_artist": track_artist,
            "station_logo": station_logo,
            "track_logo": track_logo,
            "debug_attrs": debug_attrs,
            "debug_queue": queue_track.get("debug", {}) if isinstance(queue_track, dict) else {},
        }

    async def _async_get_queue_track_metadata(
        self, entity_id: str, attrs: dict[str, Any]
    ) -> dict[str, str]:
        """Try reading current track metadata from Music Assistant queue APIs."""
        debug: dict[str, Any] = {"attempts": []}
        candidates_for_call: list[tuple[str, dict[str, Any]]] = []
        active_queue = attrs.get("active_queue")
        candidates_for_call.append(("get_queue", {"entity_id": entity_id}))
        if isinstance(active_queue, str) and active_queue.strip():
            candidates_for_call.append(("get_queue", {"queue_id": active_queue.strip()}))
        candidates_for_call.append(("get_queue", {"player_id": entity_id}))

        response: dict[str, Any] | None = None
        for service_name, payload in candidates_for_call:
            if not self.hass.services.has_service("music_assistant", service_name):
                debug["attempts"].append(
                    {"service": service_name, "payload": payload, "status": "service_missing"}
                )
                continue
            try:
                raw = await self._async_call_music_assistant(
                    service_name,
                    payload,
                    blocking=True,
                    return_response=True,
                )
                debug["attempts"].append(
                    {
                        "service": service_name,
                        "payload": payload,
                        "status": "ok",
                        "response_type": type(raw).__name__,
                    }
                )
                if isinstance(raw, dict):
                    response = raw
                    break
            except Exception as err:
                debug["attempts"].append(
                    {
                        "service": service_name,
                        "payload": payload,
                        "status": "error",
                        "error": str(err),
                    }
                )

        if not isinstance(response, dict):
            return {"title": "", "artist": "", "logo": "", "debug": debug}

        candidates: list[dict[str, Any]] = []
        for key in ("current_item", "item", "queue_item", "now_playing"):
            value = response.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        items = response.get("items")
        if isinstance(items, list) and items:
            first_item = items[0]
            if isinstance(first_item, dict):
                candidates.append(first_item)

        for item in candidates:
            title = ""
            artist = ""
            logo = ""
            for title_key in ("name", "title", "media_title", "sort_name"):
                raw = item.get(title_key)
                if isinstance(raw, str) and raw.strip():
                    title = raw.strip()
                    break
            for artist_key in ("artist", "artists", "media_artist", "subtitle"):
                raw = item.get(artist_key)
                if isinstance(raw, str) and raw.strip():
                    artist = raw.strip()
                    break
                if isinstance(raw, list) and raw:
                    first = raw[0]
                    if isinstance(first, str) and first.strip():
                        artist = first.strip()
                        break
                    if isinstance(first, dict):
                        first_name = first.get("name")
                        if isinstance(first_name, str) and first_name.strip():
                            artist = first_name.strip()
                            break
            img = item.get("image") if isinstance(item.get("image"), str) else ""
            if img:
                logo = img
            metadata = item.get("metadata")
            if not logo and isinstance(metadata, dict):
                metadata_image = metadata.get("image")
                if isinstance(metadata_image, str) and metadata_image.strip():
                    logo = metadata_image.strip()
                if not logo and isinstance(metadata.get("images"), list) and metadata["images"]:
                    first_img = metadata["images"][0]
                    if isinstance(first_img, dict):
                        candidate = first_img.get("url") or first_img.get("path") or ""
                        if isinstance(candidate, str):
                            logo = candidate
            if title and not self._looks_like_station_label(title):
                debug["matched"] = {"title": title, "artist": artist}
                return {"title": title, "artist": artist, "logo": logo, "debug": debug}
        return {"title": "", "artist": "", "logo": "", "debug": debug}

    async def _async_lookup_track_from_radio_playlists(self, station_name: str) -> dict[str, str]:
        """Fallback lookup from radio-playlists.de when player metadata is missing."""
        entries = await self._async_lookup_playlist_entries(station_name)
        if not entries:
            return {"title": "", "artist": "", "logo": ""}
        now_utc = dt_util.utcnow()
        visible = [item for item in entries if self._is_playlist_entry_visible_now(item, now_utc)]
        if not visible:
            return {"title": "", "artist": "", "logo": ""}
        latest = visible[-1]
        logo = await self._async_lookup_track_logo(latest["title"], latest["artist"])
        return {"title": latest["title"], "artist": latest["artist"], "logo": logo}

    async def _async_lookup_playlist_entries(self, station_name: str) -> list[dict[str, Any]]:
        """Read recent station playlist entries from radio-playlists.de."""
        slug = self._radio_playlists_slug(station_name)
        if not slug:
            return []

        session = async_get_clientsession(self.hass)
        url = f"https://www.radio-playlists.de/sender/{slug}.php"
        try:
            async with session.get(
                url,
                timeout=8,
                headers={"User-Agent": "HomeAssistant/internet_radio_ma"},
            ) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
        except (ClientError, TimeoutError, ValueError):
            return []

        # Convert page to plain text and parse the first playlist item.
        cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
        text = re.sub(r"(?is)<[^>]+>", "\n", cleaned)
        text = html_lib.unescape(text)
        text = re.sub(r"\r", "", text)
        text = re.sub(r"\n{2,}", "\n", text)

        # Typical block:
        # 11:45
        # Scooter
        # How Much Is the Fish
        pattern = re.compile(r"(?m)^\s*(\d{1,2}:\d{2})\s*$\n+\s*([^\n]+)\n+\s*([^\n]+)\s*$")
        local_now = dt_util.now()
        entries: list[dict[str, Any]] = []
        for match in pattern.finditer(text):
            time_str = match.group(1).strip()
            artist = match.group(2).strip()
            title = match.group(3).strip()
            if not artist or not title:
                continue
            if title.lower().endswith("min.") or artist.lower().endswith("min."):
                continue
            if self._looks_like_station_label(title):
                continue
            played_local = self._playlist_time_to_local_datetime(time_str, local_now)
            if played_local is None:
                continue
            entries.append(
                {
                    "station_name": station_name,
                    "artist": artist,
                    "title": title,
                    "played_at_local": played_local,
                    "played_at_utc": played_local.astimezone(dt_util.UTC),
                }
            )
        entries.sort(key=lambda item: item["played_at_utc"])
        return entries

    def _playlist_time_to_local_datetime(
        self, value: str, local_now: datetime
    ) -> datetime | None:
        """Convert playlist hh:mm value to timezone-aware local datetime."""
        match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # Only shift to previous day on true day-wrap cases (near midnight),
        # not for upcoming titles that are a few minutes in the future.
        if candidate - local_now > timedelta(hours=12):
            candidate = candidate - timedelta(days=1)
        return candidate

    def _radio_playlists_slug(self, station_name: str) -> str:
        """Map common station names to radio-playlists.de sender slug."""
        normalized = station_name.strip().lower()
        mappings = {
            "ndr 2 livestream": "ndr2",
            "ndr2 livestream": "ndr2",
            "ndr 2": "ndr2",
            "n-joy livestream": "n-joy",
            "n-joy": "n-joy",
            "n joy": "n-joy",
            "radio ffn": "radio-ffn",
            "ffn": "radio-ffn",
            "meer radio": "meer-radio",
        }
        for key, slug in mappings.items():
            if key in normalized:
                return slug
        return ""

    def _build_debug_attrs(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Expose focused debug metadata for radio-title troubleshooting."""
        interesting_exact = (
            "media_title",
            "media_artist",
            "media_channel",
            "source",
            "station_name",
            "stream_title",
            "stream_artist",
            "icy_title",
            "icy_name",
            "now_playing",
            "current_title",
            "current_artist",
            "song_title",
            "song_artist",
            "np_title",
            "np_artist",
            "app_name",
        )
        nested_exact = (
            "media_metadata",
            "metadata",
            "extra",
            "streamdetails",
            "radio_metadata",
            "current_item",
            "queue_item",
            "item",
            "data",
        )

        dump: dict[str, Any] = {
            "all_keys": sorted([str(key) for key in attrs.keys()]),
            "selected": {},
            "nested": {},
        }
        selected: dict[str, Any] = {}
        for key in interesting_exact:
            value = attrs.get(key)
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                selected[key] = value
            elif isinstance(value, list):
                selected[key] = [str(v) for v in value[:5]]
            elif isinstance(value, dict):
                selected[key] = {str(k): str(v) for k, v in list(value.items())[:8]}
            else:
                selected[key] = str(value)
        dump["selected"] = selected

        nested_dump: dict[str, Any] = {}
        for key in nested_exact:
            value = attrs.get(key)
            if isinstance(value, dict):
                nested_dump[key] = {str(k): str(v) for k, v in list(value.items())[:16]}
        dump["nested"] = nested_dump
        return dump

    def _extract_station_name(self, attrs: dict[str, Any]) -> str:
        """Return a stable station name and avoid RDS track metadata."""
        candidates = [
            attrs.get("media_channel"),
            attrs.get("source"),
            attrs.get("media_radio_station"),
            attrs.get("station_name"),
            attrs.get("app_name"),
        ]
        for value in candidates:
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if (
                not cleaned
                or self._looks_like_track_metadata(cleaned)
                or self._looks_like_generic_station_name(cleaned)
            ):
                continue
            self._last_station_name = cleaned
            return cleaned

        if self._last_station_name:
            return self._last_station_name
        return ""

    def _looks_like_track_metadata(self, value: str) -> bool:
        """Heuristic: reject strings that look like artist/title RDS payloads."""
        lower = value.lower()
        if any(marker in lower for marker in (" feat. ", " ft. ", " x ")):
            return True
        for separator in (" - ", " | "):
            if separator not in value:
                continue
            left, right = value.split(separator, 1)
            left_words = len(left.strip().split())
            right_words = len(right.strip().split())
            # Typical RDS shape: "Artist - Song Title".
            if left_words >= 1 and left_words <= 5 and right_words >= 2:
                return True
        return False

    def _looks_like_generic_station_name(self, value: str) -> bool:
        """Ignore generic container/source labels that are not real stations."""
        normalized = value.strip().lower()
        generic_names = {
            "music assistant queue",
            "music assistant",
            "queue",
            "default queue",
            "radio",
        }
        return normalized in generic_names

    def _extract_track_metadata(self, attrs: dict[str, Any]) -> tuple[str, str]:
        """Normalize title/artist from varying player metadata layouts."""
        title = ""
        artist = ""

        title_candidates = (
            "stream_title",
            "icy_title",
            "now_playing",
            "current_title",
            "song_title",
            "np_title",
            "media_title",
            "media_series_title",
            "title",
        )
        artist_candidates = (
            "stream_artist",
            "current_artist",
            "song_artist",
            "np_artist",
            "media_artist",
            "artist",
        )

        for key in title_candidates:
            value = attrs.get(key)
            if isinstance(value, str) and value.strip():
                title = value.strip()
                break
        for key in artist_candidates:
            value = attrs.get(key)
            if isinstance(value, str) and value.strip():
                artist = value.strip()
                break

        if not title or self._looks_like_station_label(title):
            nested_title, nested_artist = self._extract_nested_track_metadata(
                attrs,
                title_candidates=title_candidates,
                artist_candidates=artist_candidates,
            )
            if nested_title:
                title = nested_title
            if nested_artist:
                artist = nested_artist

        # Some players expose stream metadata as nested dict.
        metadata_obj = attrs.get("media_metadata")
        if isinstance(metadata_obj, dict):
            if not title:
                md_title = metadata_obj.get("title")
                if isinstance(md_title, str) and md_title.strip():
                    title = md_title.strip()
            if not artist:
                md_artist = metadata_obj.get("artist")
                if isinstance(md_artist, str) and md_artist.strip():
                    artist = md_artist.strip()

        if not isinstance(title, str):
            title = ""
        if not isinstance(artist, str):
            artist = ""

        title = title.strip()
        artist = artist.strip()

        if not artist or self._looks_like_station_label(title):
            for stream_key in ("stream_title", "icy_title", "now_playing", "np_title"):
                value = attrs.get(stream_key)
                if not isinstance(value, str):
                    continue
                parsed_title, parsed_artist = self._parse_artist_title_string(value.strip())
                if parsed_title:
                    title = parsed_title
                if parsed_artist:
                    artist = parsed_artist
                if parsed_title:
                    break

        if title and not artist:
            for sep in (" - ", " | "):
                if sep not in title:
                    continue
                left, right = title.split(sep, 1)
                left = left.strip()
                right = right.strip()
                if left and right:
                    artist = left
                    title = right
                    break

        if artist and title:
            prefix_dash = f"{artist} - "
            prefix_pipe = f"{artist} | "
            if title.startswith(prefix_dash):
                title = title.removeprefix(prefix_dash).strip()
            elif title.startswith(prefix_pipe):
                title = title.removeprefix(prefix_pipe).strip()

        if self._looks_like_station_label(title):
            return "", ""

        return title, artist

    def _extract_nested_track_metadata(
        self,
        attrs: dict[str, Any],
        *,
        title_candidates: tuple[str, ...],
        artist_candidates: tuple[str, ...],
    ) -> tuple[str, str]:
        """Try extracting track metadata from nested media payloads."""
        nested_keys = (
            "media_metadata",
            "metadata",
            "extra",
            "streamdetails",
            "radio_metadata",
            "current_item",
            "queue_item",
            "item",
            "data",
        )
        title = ""
        artist = ""
        for key in nested_keys:
            payload = attrs.get(key)
            if not isinstance(payload, dict):
                continue
            if not title:
                for title_key in (*title_candidates, "name", "subtitle"):
                    raw_title = payload.get(title_key)
                    if isinstance(raw_title, str) and raw_title.strip():
                        title = raw_title.strip()
                        break
            if not artist:
                for artist_key in (*artist_candidates, "performer"):
                    raw_artist = payload.get(artist_key)
                    if isinstance(raw_artist, str) and raw_artist.strip():
                        artist = raw_artist.strip()
                        break
            if title and artist:
                break
        return title, artist

    def _parse_artist_title_string(self, value: str) -> tuple[str, str]:
        """Parse common stream metadata format 'Artist - Title'."""
        if not value:
            return "", ""
        for sep in (" - ", " | "):
            if sep not in value:
                continue
            left, right = value.split(sep, 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                return right, left
        return value.strip(), ""

    def _looks_like_station_label(self, value: str) -> bool:
        """Detect labels that are likely station/source names, not song titles."""
        lower = value.strip().lower()
        if not lower:
            return True
        station_markers = (
            "livestream",
            "live stream",
            "radio",
            "music assistant queue",
            "queue",
        )
        return any(marker in lower for marker in station_markers)

    def _is_track_like_station(self, station_name: str, track_title: str, track_artist: str) -> bool:
        """Return True when current track metadata likely does not represent a real song."""
        if track_artist:
            return False
        if self._looks_like_station_label(track_title):
            return True
        if station_name and track_title and station_name.strip().lower() == track_title.strip().lower():
            return True
        return False

    async def _async_lookup_station_logo(self, station_name: str) -> str:
        """Fallback station logo lookup using Radio Browser."""
        session = async_get_clientsession(self.hass)
        candidates = self._station_name_candidates(station_name)
        urls: list[str] = []
        for name in candidates:
            urls.append(
                "https://all.api.radio-browser.info/json/stations/bynameexact/"
                + quote_plus(name)
            )
            urls.append(
                "https://all.api.radio-browser.info/json/stations/search?"
                f"name={quote_plus(name)}&limit=5&hidebroken=true"
            )
            urls.append(
                "https://all.api.radio-browser.info/json/stations/search?"
                f"name={quote_plus(name)}&limit=15"
            )

        for url in urls:
            try:
                async with session.get(url, timeout=8) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    logo = self._extract_best_logo(data)
                    if logo:
                        return logo
            except (ClientError, TimeoutError, ValueError):
                continue
        return ""

    async def _async_lookup_track_logo(self, track_title: str, track_artist: str = "") -> str:
        """Fallback track cover lookup via iTunes search API."""
        session = async_get_clientsession(self.hass)
        queries: list[str] = []
        if track_artist and track_title:
            queries.append(f"{track_artist} {track_title}")
        if track_title:
            queries.append(track_title)
        if track_artist:
            queries.append(track_artist)

        for query in queries:
            url = f"https://itunes.apple.com/search?term={quote_plus(query)}&entity=song&limit=3"
            try:
                async with session.get(
                    url,
                    timeout=8,
                    headers={"User-Agent": "HomeAssistant/internet_radio_ma"},
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    results = data.get("results", []) if isinstance(data, dict) else []
                    if not results:
                        continue
                    for item in results:
                        if not isinstance(item, dict):
                            continue
                        logo = item.get("artworkUrl100") or item.get("artworkUrl60") or ""
                        if logo:
                            return logo
            except (ClientError, TimeoutError, ValueError):
                continue
        return ""

    async def _async_normalize_favorites(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize favorite items and enrich logos when missing."""
        normalized: list[dict[str, Any]] = []
        for item in items:
            fav = self._normalize_favorite_item(item)
            if not self._is_usable_logo_url(fav["logo"]) and fav["name"]:
                fav["logo"] = await self._async_lookup_station_logo_cached(fav["name"])
            if (
                not self._is_usable_logo_url(fav["logo"])
                and fav.get("stream_url")
                and isinstance(fav.get("stream_url"), str)
            ):
                fav["logo"] = self._logo_from_stream_url(fav["stream_url"])
            if not self._is_usable_logo_url(fav["logo"]) and fav["name"]:
                fav["logo"] = self._logo_from_station_name(fav["name"])
            normalized.append(fav)
        return normalized

    def _normalize_favorite_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normalize MA favorite item payload."""
        metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
        images = metadata.get("images", []) if isinstance(metadata, dict) else []
        logo = ""
        if isinstance(item, dict):
            logo = (
                item.get("image")
                or item.get("image_url")
                or item.get("thumbnail")
                or ""
            )
        if images and isinstance(images, list):
            first_img = images[0]
            if isinstance(first_img, dict):
                logo = (
                    first_img.get("url")
                    or first_img.get("path")
                    or first_img.get("image_url")
                    or ""
                )

        provider_mappings = item.get("provider_mappings", []) if isinstance(item, dict) else []
        stream_url = ""
        if provider_mappings and isinstance(provider_mappings, list):
            for mapping in provider_mappings:
                if isinstance(mapping, dict) and mapping.get("url"):
                    stream_url = mapping["url"]
                    break
                if not logo and isinstance(mapping, dict):
                    logo = (
                        mapping.get("image_url")
                        or mapping.get("logo")
                        or mapping.get("icon")
                        or logo
                    )
                if not stream_url and isinstance(mapping, dict):
                    stream_url = (
                        mapping.get("stream_url")
                        or mapping.get("uri")
                        or mapping.get("audio_url")
                        or stream_url
                    )

        if not logo:
            logo = (
                metadata.get("image_url", "")
                if isinstance(metadata, dict)
                else ""
            )
        if not logo and isinstance(metadata, dict):
            logo = metadata.get("logo") or metadata.get("thumb") or ""
        if not stream_url and isinstance(item, dict):
            stream_url = item.get("url") or item.get("stream_url") or ""
        if not stream_url and isinstance(metadata, dict):
            stream_url = metadata.get("url") or metadata.get("stream_url") or ""

        return {
            "id": item.get("item_id") or item.get("id") or item.get("uri") or item.get("name"),
            "name": item.get("name", ""),
            "uri": item.get("uri", ""),
            "stream_url": stream_url,
            "logo": logo,
        }

    def _append_history(
        self,
        station_name: str,
        track_title: str,
        track_artist: str,
        station_logo: str,
        track_logo: str,
    ) -> None:
        """Maintain global latest track history (max 10)."""
        track_key = normalize_track_key(station_name, f"{track_artist}::{track_title}")
        if self._last_global_track_key == track_key:
            return
        if self._history_contains_entry(
            station_name,
            track_artist,
            track_title,
            dt_util.utcnow(),
            tolerance_seconds=DUPLICATE_TIME_WINDOW_SECONDS,
        ):
            return

        final_station_logo = ""
        if station_name:
            # Prefer deterministic station-name based logo to avoid stale logo carryover
            # when the player switches streams and keeps old entity_picture briefly.
            final_station_logo = self._station_logo_cache.get(station_name.strip().lower(), "")
            if not final_station_logo:
                final_station_logo = self._logo_from_station_name(station_name)
        if not final_station_logo and isinstance(station_logo, str):
            final_station_logo = station_logo

        self._global_history.append(
            {
                "title": track_title,
                "artist": track_artist,
                "logo": track_logo,
                "station_name": station_name,
                "station_logo": final_station_logo,
                "played_at": utc_iso_now(),
            }
        )
        self._last_global_track_key = track_key

    def _should_run_backfill(self, now_utc: datetime) -> bool:
        """Run expensive web backfill max once per minute."""
        if self._last_backfill_check is None:
            return True
        return (now_utc - self._last_backfill_check).total_seconds() >= BACKFILL_INTERVAL_SECONDS

    def _is_player_active(self, player_state: str) -> bool:
        """Detect active playback states for track-history backfill."""
        normalized = (player_state or "").strip().lower()
        return normalized in {"playing", "buffering"}

    def _track_playback_window(self, player_snapshot: dict[str, Any], now_utc: datetime) -> None:
        """Track when a concrete station actually played on the target player."""
        station_name = player_snapshot.get("station_name", "")
        state = player_snapshot.get("state", "")
        is_active = bool(station_name) and self._is_player_active(state)

        if is_active:
            if self._active_playback_window is None:
                self._active_playback_window = {
                    "station_name": station_name,
                    "started_at": now_utc,
                    "ended_at": now_utc,
                }
                return
            if self._active_playback_window.get("station_name") != station_name:
                self._close_active_playback_window(now_utc)
                self._active_playback_window = {
                    "station_name": station_name,
                    "started_at": now_utc,
                    "ended_at": now_utc,
                }
                return
            self._active_playback_window["ended_at"] = now_utc
            return

        self._close_active_playback_window(now_utc)

    def _close_active_playback_window(self, now_utc: datetime) -> None:
        """Close and store current playback window if present."""
        if self._active_playback_window is None:
            return
        self._active_playback_window["ended_at"] = now_utc
        self._playback_windows.append(self._active_playback_window)
        self._active_playback_window = None

    async def _async_backfill_history_for_station(
        self,
        station_name: str,
        station_logo: str,
        now_utc: datetime,
    ) -> None:
        """Add missing titles from playlist provider for active station windows."""
        if not station_name:
            return
        entries = await self._async_lookup_playlist_entries(station_name)
        if not entries:
            return
        windows = self._collect_station_windows(station_name, now_utc)
        if not windows:
            return

        for item in entries:
            played_at_utc = item.get("played_at_utc")
            if not isinstance(played_at_utc, datetime):
                continue
            if played_at_utc > now_utc:
                continue
            if not self._is_in_windows(played_at_utc, windows):
                continue
            artist = item.get("artist", "")
            title = item.get("title", "")
            if not title:
                continue
            if self._history_contains_entry(station_name, artist, title, played_at_utc):
                continue
            track_logo = await self._async_lookup_track_logo(title, artist)
            self._append_history_with_timestamp(
                station_name=station_name,
                track_title=title,
                track_artist=artist,
                station_logo=station_logo,
                track_logo=track_logo,
                played_at=played_at_utc,
            )

    def _collect_station_windows(
        self, station_name: str, now_utc: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Collect recent playback windows for station, including active one."""
        station_key = station_name.strip().lower()
        windows: list[tuple[datetime, datetime]] = []
        recent_cutoff = now_utc - timedelta(hours=6)

        for window in self._playback_windows:
            if window.get("station_name", "").strip().lower() != station_key:
                continue
            started_at = window.get("started_at")
            ended_at = window.get("ended_at")
            if not isinstance(started_at, datetime) or not isinstance(ended_at, datetime):
                continue
            if ended_at < recent_cutoff:
                continue
            windows.append((started_at - BACKFILL_WINDOW_LOOKBACK, ended_at))

        active = self._active_playback_window
        if active and active.get("station_name", "").strip().lower() == station_key:
            started_at = active.get("started_at")
            ended_at = active.get("ended_at") or now_utc
            if isinstance(started_at, datetime) and isinstance(ended_at, datetime):
                windows.append((started_at - BACKFILL_WINDOW_LOOKBACK, ended_at))

        return windows

    def _is_in_windows(
        self, played_at_utc: datetime, windows: list[tuple[datetime, datetime]]
    ) -> bool:
        """Check whether track timestamp is inside any recorded active window."""
        for started_at, ended_at in windows:
            if started_at <= played_at_utc <= ended_at:
                return True
        return False

    def _history_contains_entry(
        self,
        station_name: str,
        track_artist: str,
        track_title: str,
        played_at: datetime,
        *,
        tolerance_seconds: int = 120,
    ) -> bool:
        """Detect duplicates by same title close to same playback minute."""
        target_key = normalize_track_key(station_name, f"{track_artist}::{track_title}")
        for item in self._global_history:
            item_key = normalize_track_key(
                item.get("station_name", ""),
                f"{item.get('artist', '')}::{item.get('title', '')}",
            )
            if item_key != target_key:
                continue
            existing_ts = self._history_sort_key(item)
            if abs((existing_ts - played_at).total_seconds()) <= tolerance_seconds:
                return True
        return False

    def _is_playlist_entry_visible_now(self, entry: dict[str, Any], now_utc: datetime) -> bool:
        """Only accept playlist entries whose start time is not in the future."""
        played_at_utc = entry.get("played_at_utc")
        if not isinstance(played_at_utc, datetime):
            return False
        return played_at_utc <= now_utc

    def _append_history_with_timestamp(
        self,
        station_name: str,
        track_title: str,
        track_artist: str,
        station_logo: str,
        track_logo: str,
        played_at: datetime,
    ) -> None:
        """Insert history entry ordered by played timestamp and trim to max size."""
        if self._history_contains_entry(
            station_name,
            track_artist,
            track_title,
            played_at,
            tolerance_seconds=DUPLICATE_TIME_WINDOW_SECONDS,
        ):
            return

        final_station_logo = ""
        if station_name:
            final_station_logo = self._station_logo_cache.get(station_name.strip().lower(), "")
            if not final_station_logo:
                final_station_logo = self._logo_from_station_name(station_name)
        if not final_station_logo and isinstance(station_logo, str):
            final_station_logo = station_logo

        entries = list(self._global_history)
        entries.append(
            {
                "title": track_title,
                "artist": track_artist,
                "logo": track_logo,
                "station_name": station_name,
                "station_logo": final_station_logo,
                "played_at": played_at.isoformat(),
            }
        )
        entries.sort(key=self._history_sort_key)
        if len(entries) > MAX_HISTORY_ITEMS:
            entries = entries[-MAX_HISTORY_ITEMS:]
        self._global_history = deque(entries, maxlen=MAX_HISTORY_ITEMS)
        last_item = self._global_history[-1] if self._global_history else None
        if last_item:
            self._last_global_track_key = normalize_track_key(
                last_item.get("station_name", ""),
                f"{last_item.get('artist', '')}::{last_item.get('title', '')}",
            )

    def _history_sort_key(self, item: dict[str, Any]) -> datetime:
        """Build robust sort key for history entries."""
        raw = item.get("played_at")
        if isinstance(raw, str) and raw:
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=dt_util.UTC)
                return parsed
            except ValueError:
                pass
        return dt_util.utcnow()

    def _resolve_ma_config_entry_id(self) -> str | None:
        """Find the active Music Assistant config entry id, if available."""
        entries = self.hass.config_entries.async_entries("music_assistant")
        if not entries:
            return None
        # Prefer loaded entries, fallback to the first one.
        loaded = [entry for entry in entries if entry.state is ConfigEntryState.LOADED]
        selected = loaded[0] if loaded else entries[0]
        return selected.entry_id

    async def _async_lookup_station_logo_cached(self, station_name: str) -> str:
        """Lookup station logo with in-memory cache."""
        cache_key = station_name.strip().lower()
        if cache_key in self._station_logo_cache:
            return self._station_logo_cache[cache_key]
        logo = await self._async_lookup_station_logo(station_name)
        self._station_logo_cache[cache_key] = logo or ""
        return logo or ""

    def _is_usable_logo_url(self, value: str) -> bool:
        """Check if logo field is likely directly renderable in HA frontend."""
        if not value:
            return False
        lower = value.lower()
        if lower.startswith(
            ("http://", "https://", "/api/", "/local/", "/", "//", "data:image/")
        ):
            return True
        # Some MA payloads return provider/image ids, not frontend-usable URLs.
        return False

    def _station_name_candidates(self, station_name: str) -> list[str]:
        """Create normalized search variants for radio station lookup."""
        raw = station_name.strip()
        if not raw:
            return []

        variants = [raw]
        simplified = re.sub(
            r"\b(livestream|live stream|stream|radio|fm|deutschland)\b",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        simplified = re.sub(r"\s+", " ", simplified).strip(" -_")
        if simplified and simplified.lower() != raw.lower():
            variants.append(simplified)

        # Keep order, remove duplicates case-insensitive.
        seen: set[str] = set()
        deduped: list[str] = []
        for item in variants:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _extract_best_logo(self, data: Any) -> str:
        """Extract first usable favicon from Radio Browser response."""
        if not isinstance(data, list):
            return ""
        for item in data:
            if not isinstance(item, dict):
                continue
            favicon = (item.get("favicon") or "").strip()
            if self._is_usable_logo_url(favicon):
                return favicon
        return ""

    def _logo_from_stream_url(self, stream_url: str) -> str:
        """Generate a best-effort favicon URL from stream host."""
        try:
            parsed = urlparse(stream_url)
            host = (parsed.hostname or "").strip()
            if not host:
                return ""
            # Public favicon mirror; does not require per-station integration.
            return f"https://icons.duckduckgo.com/ip3/{host}.ico"
        except Exception:
            return ""

    def _logo_from_station_name(self, station_name: str) -> str:
        """Best-effort static mappings for common station names."""
        name = station_name.strip().lower()
        if not name:
            return ""

        mappings = {
            "ndr 2": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0a/NDR_2_logo.svg/512px-NDR_2_logo.svg.png",
            "n-joy": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ea/N-Joy_logo_2021.svg/512px-N-Joy_logo_2021.svg.png",
            "ffn": "https://www.ffn.de/fileadmin/templates/ffn/favicon/apple-touch-icon.png",
            "meer radio": "https://www.meerradio.de/wp-content/uploads/2022/08/cropped-meerradio-logo-512x512.png",
        }

        for key, logo in mappings.items():
            if key in name:
                return logo
        return ""
