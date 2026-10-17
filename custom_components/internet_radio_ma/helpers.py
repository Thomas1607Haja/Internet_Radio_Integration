"""Helper methods for internet_radio_ma."""

from __future__ import annotations

import re

from homeassistant.util import dt as dt_util


def utc_iso_now() -> str:
    """Return current UTC time as ISO string."""
    return dt_util.utcnow().isoformat()


def normalize_track_key(station_name: str, track_title: str) -> str:
    """Generate a stable dedupe key for history entries."""
    norm_station = re.sub(r"\s+", " ", station_name.strip().lower())
    norm_track = re.sub(r"\s+", " ", track_title.strip().lower())
    return f"{norm_station}::{norm_track}"
