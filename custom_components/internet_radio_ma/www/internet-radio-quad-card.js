const CARD_TYPE = "internet-radio-quad-card";
const EDITOR_TYPE = "internet-radio-quad-card-editor";
const MINI_CARD_TYPE = "internet-radio-mini-nowplaying-card";
const MINI_EDITOR_TYPE = "internet-radio-mini-nowplaying-card-editor";
const CARD_VERSION = "1.0.24";

const DEFAULT_CONFIG = {
  title: "Internet Radio",
  pageSize: 9,
  fill_screen: true,
  touch_mode: true,
  theme_mode: "auto",
};

function fireEvent(node, type, detail, options = {}) {
  const event = new Event(type, {
    bubbles: options.bubbles ?? true,
    cancelable: options.cancelable ?? false,
    composed: options.composed ?? true,
  });
  event.detail = detail;
  node.dispatchEvent(event);
  return event;
}

function normalizeNavigationPath(path) {
  if (!path || typeof path !== "string") return "";
  const trimmed = path.trim();
  if (!trimmed) return "";
  if (
    trimmed.startsWith("http://") ||
    trimmed.startsWith("https://") ||
    trimmed.startsWith("/")
  ) {
    return trimmed;
  }
  return `/${trimmed}`;
}

class InternetRadioQuadCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  static getStubConfig() {
    return {
      type: `custom:${CARD_TYPE}`,
      entity: "sensor.internet_radio_dashboard",
      media_player: "media_player.your_player",
      ...DEFAULT_CONFIG,
    };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    if (!config.media_player) throw new Error("media_player is required");
    this._config = { ...DEFAULT_CONFIG, ...config };
    this._page = 0;
    if (typeof this._lastVolumePercent !== "number") this._lastVolumePercent = 50;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 12;
  }

  _entity() {
    return this._hass?.states?.[this._config.entity];
  }

  _ensureRuntimeCaches() {
    if (!this._resolvedMediaMap) this._resolvedMediaMap = new Map();
    if (!this._pendingMediaResolves) this._pendingMediaResolves = new Set();
    if (!this._deviceImageDebug) this._deviceImageDebug = {};
    if (!this._deviceImageCache) this._deviceImageCache = {};
  }

  _mediaSourceIdFromValue(value) {
    if (!value) return "";
    if (typeof value === "string") {
      const raw = value.trim();
      if (!raw) return "";
      if (raw.startsWith("media-source://")) return raw;
      if (raw.startsWith("/media/local/")) {
        const relPath = raw.slice("/media/local/".length);
        return `media-source://media_source/local/${relPath}`;
      }
      return "";
    }
    if (typeof value === "object") {
      if (typeof value.media_content_id === "string" && value.media_content_id.trim()) {
        return this._mediaSourceIdFromValue(value.media_content_id);
      }
      if (typeof value.id === "string" && value.id.trim()) {
        return this._mediaSourceIdFromValue(value.id);
      }
    }
    return "";
  }

  _queueResolveMediaSource(value) {
    this._ensureRuntimeCaches();
    const mediaSourceId = this._mediaSourceIdFromValue(value);
    if (!mediaSourceId) return;
    if (!this._hass?.callWS) return;
    if (this._resolvedMediaMap.has(mediaSourceId)) return;
    if (this._pendingMediaResolves.has(mediaSourceId)) return;

    this._pendingMediaResolves.add(mediaSourceId);
    this._hass
      .callWS({
        type: "media_source/resolve_media",
        media_content_id: mediaSourceId,
      })
      .then((result) => {
        const resolvedUrl =
          (typeof result?.url === "string" && result.url.trim()) ||
          (typeof result?.path === "string" && result.path.trim()) ||
          "";
        this._resolvedMediaMap.set(mediaSourceId, {
          url: resolvedUrl,
          error: resolvedUrl ? "" : "resolve_returned_no_url",
          updated: new Date().toISOString(),
        });
      })
      .catch((err) => {
        this._resolvedMediaMap.set(mediaSourceId, {
          url: "",
          error: err?.message || String(err),
          updated: new Date().toISOString(),
        });
      })
      .finally(() => {
        this._pendingMediaResolves.delete(mediaSourceId);
        this._render();
      });
  }

  _resolvedMediaUrl(value) {
    this._ensureRuntimeCaches();
    const mediaSourceId = this._mediaSourceIdFromValue(value);
    if (!mediaSourceId) return "";
    return this._resolvedMediaMap.get(mediaSourceId)?.url || "";
  }

  async _tryLoadDeviceImageBlob(imgEl, srcUrl) {
    if (!imgEl || !srcUrl || !this._hass) return false;
    const headers = {};
    const token = this._hass?.auth?.data?.access_token;
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(srcUrl, {
      method: "GET",
      credentials: "same-origin",
      headers,
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`http_${response.status}`);
    const blob = await response.blob();
    if (blob?.type && !blob.type.startsWith("image/")) {
      throw new Error(`invalid_mime_${blob.type}`);
    }
    const oldObjectUrl = imgEl.getAttribute("data-object-url");
    if (oldObjectUrl) URL.revokeObjectURL(oldObjectUrl);
    const objectUrl = URL.createObjectURL(blob);
    imgEl.setAttribute("data-object-url", objectUrl);
    imgEl.src = objectUrl;
    return true;
  }

  _logoUrl(value) {
    if (!value || typeof value !== "string") return "";
    const trimmed = value.trim();
    if (!trimmed) return "";
    if (trimmed.startsWith("media-source://")) {
      return this._resolveMediaSelection(trimmed);
    }
    if (trimmed.startsWith("/media/local/")) {
      return this._hass?.hassUrl ? this._hass.hassUrl(trimmed) : trimmed;
    }
    if (trimmed.startsWith("//")) return `${window.location.protocol}${trimmed}`;
    if (trimmed.startsWith("/")) {
      return this._hass?.hassUrl ? this._hass.hassUrl(trimmed) : trimmed;
    }
    if (
      !trimmed.startsWith("http://") &&
      !trimmed.startsWith("https://") &&
      !trimmed.startsWith("data:image/") &&
      !trimmed.startsWith("blob:")
    ) {
      return "";
    }
    return trimmed;
  }

  _resolveMediaSelection(value) {
    if (!value) return "";
    if (typeof value === "string") {
      const raw = value.trim();
      if (!raw) return "";
      if (raw.startsWith("media-source://")) {
        const withoutScheme = raw.slice("media-source://".length);
        const encodedPath = withoutScheme
          .split("/")
          .map((part) => encodeURIComponent(part))
          .join("/");
        return this._logoUrl(`/api/media_source_proxy/${encodedPath}`);
      }
      if (raw.startsWith("/media/local/")) {
        return this._logoUrl(raw);
      }
      return this._logoUrl(raw);
    }
    if (typeof value !== "object") return "";

    const mediaContentId = value?.media_content_id;
    if (typeof mediaContentId === "string" && mediaContentId.trim()) {
      const normalized = this._resolveMediaSelection(mediaContentId);
      if (normalized) return normalized;
    }

    const thumbnail = value?.metadata?.thumbnail;
    if (typeof thumbnail === "string" && thumbnail.trim()) {
      const normalizedThumb = this._resolveMediaSelection(thumbnail);
      if (normalizedThumb) return normalizedThumb;
    }

    const id = value?.id;
    if (typeof id === "string" && id.trim()) {
      if (id.startsWith("media-source://")) {
        const withoutScheme = id.slice("media-source://".length);
        const encodedPath = withoutScheme
          .split("/")
          .map((part) => encodeURIComponent(part))
          .join("/");
        return this._logoUrl(`/api/media_source_proxy/${encodedPath}`);
      }
    }
    return "";
  }

  _mediaSourceCandidates(rawValue) {
    if (!rawValue || typeof rawValue !== "string") return [];
    const raw = rawValue.trim();
    if (!raw) return [];
    if (!raw.startsWith("media-source://")) {
      const direct = this._logoUrl(raw);
      return direct ? [direct] : [];
    }

    const withoutScheme = raw.slice("media-source://".length);
    this._queueResolveMediaSource(raw);
    const resolved = this._resolvedMediaUrl(raw);
    const splitEncoded = withoutScheme
      .split("/")
      .map((part) => encodeURIComponent(part))
      .join("/");
    const fullEncoded = encodeURIComponent(withoutScheme);

    const candidates = [
      resolved,
      `/api/media_source_proxy/${splitEncoded}`,
      `/api/media_source_proxy/${fullEncoded}`,
    ];
    if (withoutScheme.startsWith("media_source/local/")) {
      const relPath = withoutScheme.slice("media_source/local/".length);
      candidates.push(`/media/local/${relPath}`);
    }

    const normalized = candidates
      .map((url) => this._logoUrl(url))
      .filter((url) => !!url);
    return Array.from(new Set(normalized));
  }

  _deviceImageCandidates(deviceState, attrs) {
    const mediaKey = deviceState === "off" ? "device_image_off_media" : "device_image_on_media";
    const attrKey = deviceState === "off" ? "device_image_off" : "device_image_on";

    const candidates = [];
    const mediaConfig = this._config?.[mediaKey];
    if (typeof mediaConfig === "string") {
      candidates.push(...this._mediaSourceCandidates(mediaConfig));
    } else if (mediaConfig && typeof mediaConfig === "object") {
      if (typeof mediaConfig.media_content_id === "string") {
        candidates.push(...this._mediaSourceCandidates(mediaConfig.media_content_id));
      }
      if (typeof mediaConfig.id === "string") {
        candidates.push(...this._mediaSourceCandidates(mediaConfig.id));
      }
      if (typeof mediaConfig.url === "string") {
        const normalized = this._logoUrl(mediaConfig.url);
        if (normalized) candidates.push(normalized);
      }
    }

    const primary = this._deviceImageUrl(deviceState, attrs);
    if (primary) candidates.unshift(primary);

    const attrFromMedia = this._resolveMediaSelection(attrs?.[attrKey]);
    if (attrFromMedia) candidates.push(attrFromMedia);

    const attrRaw = this._logoUrl(attrs?.[attrKey] || "");
    if (attrRaw) candidates.push(attrRaw);

    return Array.from(new Set(candidates.filter((value) => !!value)));
  }

  _historyStationLogo(item, favorites) {
    const stationName = String(item?.station_name || "").trim().toLowerCase();
    if (!stationName) return this._logoUrl(item?.station_logo || "") || this._logoUrl(item?.logo || "");

    const exact = favorites.find(
      (fav) => String(fav?.name || "").trim().toLowerCase() === stationName
    );
    if (exact?.logo) return this._logoUrl(exact.logo);

    const fuzzy = favorites.find((fav) => {
      const favName = String(fav?.name || "").trim().toLowerCase();
      return favName.includes(stationName) || stationName.includes(favName);
    });
    if (fuzzy?.logo) return this._logoUrl(fuzzy.logo);

    return this._logoUrl(item?.station_logo || "") || this._logoUrl(item?.logo || "");
  }

  _configuredImageUrl(entityKey, mediaKey, urlKey) {
    const mediaSelected = this._resolveMediaSelection(this._config?.[mediaKey]);
    if (mediaSelected) return mediaSelected;

    const imageEntityId = this._config?.[entityKey];
    if (imageEntityId && this._hass?.states?.[imageEntityId]) {
      const imageEntity = this._hass.states[imageEntityId];
      const attrUrl =
        imageEntity?.attributes?.entity_picture ||
        imageEntity?.attributes?.image_url ||
        "";
      if (attrUrl) return this._logoUrl(attrUrl);
      if (typeof imageEntity?.state === "string") {
        const stateUrl = imageEntity.state.trim();
        if (
          stateUrl.startsWith("/") ||
          stateUrl.startsWith("http://") ||
          stateUrl.startsWith("https://") ||
          stateUrl.startsWith("//")
        ) {
          return this._logoUrl(stateUrl);
        }
      }
    }
    return this._logoUrl(this._config?.[urlKey] || "");
  }

  _deviceImageUrl(deviceState, attrs) {
    const configMediaKey = deviceState === "off" ? "device_image_off_media" : "device_image_on_media";
    const configEntityKey = deviceState === "off" ? "device_image_off_entity" : "device_image_on_entity";
    const configUrlKey = deviceState === "off" ? "device_image_off" : "device_image_on";
    const attrKey = deviceState === "off" ? "device_image_off" : "device_image_on";

    const fromConfig = this._configuredImageUrl(configEntityKey, configMediaKey, configUrlKey);
    if (fromConfig) return fromConfig;

    const fromAttrMedia = this._resolveMediaSelection(attrs?.[attrKey]);
    if (fromAttrMedia) return fromAttrMedia;

    return this._logoUrl(attrs?.[attrKey] || "");
  }

  _formatPlayedAt(value) {
    if (!value || typeof value !== "string") return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "-";
    const now = new Date();
    const sameDay = now.toDateString() === date.toDateString();
    if (sameDay) {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleString([], {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  _render() {
    if (!this._hass || !this._config) return;

    if (!this.content) {
      this.innerHTML = "";
      this.content = document.createElement("ha-card");
      this.content.classList.add("ir-card");
      this.appendChild(this.content);
    }

    const entity = this._entity();
    if (!entity) {
      this.content.innerHTML = `<div class="missing">Entity ${this._config.entity} not found</div>`;
      return;
    }

    const attrs = entity.attributes || {};
    const favorites = attrs.favorites || [];
    const pageSize = Math.min(this._config.pageSize || 9, 9);
    const totalPages = Math.max(1, Math.ceil(favorites.length / pageSize));
    if (this._page > totalPages - 1) this._page = totalPages - 1;
    const start = this._page * pageSize;
    const visibleFavorites = favorites.slice(start, start + pageSize);

    const station = attrs.current_station || {};
    const track = attrs.current_track || {};
    const trackHistory = attrs.track_history || [];
    const deviceState = attrs.device_state || "unknown";
    const isOff = deviceState === "off";
    const mpState = this._hass.states[this._config.media_player];
    const rawVolume = mpState?.attributes?.volume_level;
    let volume = this._lastVolumePercent ?? 50;
    if (isOff) {
      volume = 0;
    } else if (typeof rawVolume === "number" && Number.isFinite(rawVolume)) {
      volume = Math.round(Math.max(0, Math.min(1, rawVolume)) * 100);
      this._lastVolumePercent = volume;
    }
    const deviceImage = this._deviceImageUrl(deviceState, attrs);
    const deviceImageCandidates = this._deviceImageCandidates(deviceState, attrs);
    const deviceImageSignature = deviceImageCandidates.join("|");
    const cachedDeviceImage = this._deviceImageCache?.[deviceState];
    const cachedDeviceImageSrc =
      cachedDeviceImage && cachedDeviceImage.signature === deviceImageSignature
        ? cachedDeviceImage.src || ""
        : "";
    const renderedTrackHistory = trackHistory.slice(0, 10).map((item) => ({
      ...item,
      resolved_station_logo: this._historyStationLogo(item, favorites),
    }));
    const isUnavailable = ["unavailable", "unknown"].includes(String(entity.state || "").toLowerCase());
    const hasTrackInfo = !!(track.title || track.artist);
    const stationName = isOff ? "Ausgeschaltet" : isUnavailable ? "Nicht verfuegbar" : station.name || "Kein Sender";
    const trackTitle = isOff || isUnavailable || !hasTrackInfo ? "Keine Titelinformationen" : track.title || "Keine Titelinformationen";
    const trackArtist = isOff || isUnavailable || !hasTrackInfo ? "" : track.artist || "";
    const stationLogo = isOff ? "" : station.logo || "";
    const trackLogo = isOff ? "" : track.logo || "";
    const deviceImageUrl = this._logoUrl(deviceImage);
    const fillHeightClass = this._config.fill_screen ? "fill-screen" : "fit-content";
    const touchClass = this._config.touch_mode ? "touch-mode" : "normal-mode";
    const themeMode = ["auto", "light", "dark"].includes(this._config.theme_mode)
      ? this._config.theme_mode
      : "auto";
    const themeClass = `theme-${themeMode}`;
    const atFirstPage = this._page <= 0;
    const atLastPage = this._page >= totalPages - 1;
    const currentStationKey = String(station.name || "").trim().toLowerCase();

    this.content.innerHTML = `
      <style>
        :host {
          --ir-space-xs: 4px;
          --ir-space-sm: 8px;
          --ir-space-md: 12px;
          --ir-space-lg: 16px;
          --ir-space-xl: 22px;
          --ir-radius-sm: 8px;
          --ir-radius-md: 12px;
          --ir-radius-lg: 16px;
          --ir-radius-pill: 999px;
          --ir-card-bg: var(--card-background-color, var(--ha-card-background, var(--primary-background-color)));
          --ir-surface: color-mix(in srgb, var(--ir-card-bg) 88%, var(--primary-background-color));
          --ir-surface-soft: color-mix(in srgb, var(--ir-card-bg) 72%, var(--secondary-background-color, var(--primary-background-color)));
          --ir-surface-strong: color-mix(in srgb, var(--ir-card-bg) 82%, var(--primary-color));
          --ir-text: var(--primary-text-color);
          --ir-muted: var(--secondary-text-color);
          --ir-accent: var(--primary-color);
          --ir-border: color-mix(in srgb, var(--divider-color) 72%, transparent);
          --ir-disabled: var(--disabled-text-color);
        }
        .theme-light,
        .theme-dark {
          --ir-card-bg: var(--card-background-color, var(--ha-card-background, var(--primary-background-color)));
          --ir-surface: color-mix(in srgb, var(--ir-card-bg) 88%, var(--primary-background-color));
          --ir-surface-soft: color-mix(in srgb, var(--ir-card-bg) 72%, var(--secondary-background-color, var(--primary-background-color)));
          --ir-surface-strong: color-mix(in srgb, var(--ir-card-bg) 82%, var(--primary-color));
        }
        .ir-card {
          box-sizing: border-box;
          padding: var(--ir-space-lg);
          border-radius: var(--ir-radius-lg);
          background: var(--ir-card-bg);
          color: var(--ir-text);
        }
        .fill-screen { min-height: 720px; height: calc(100vh - 86px); }
        .fit-content { min-height: 640px; height: auto; }
        .grid {
          display: grid;
          grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
          grid-template-rows: minmax(210px, auto) minmax(320px, 1fr);
          gap: var(--ir-space-lg);
          height: 100%;
        }
        .quad {
          background: transparent;
          border: 0;
          border-radius: var(--ir-radius-lg);
          padding: 0;
          overflow: hidden;
        }
        .quad-inner,
        .player-panel,
        .volume-panel,
        .history-panel,
        .favorites-panel {
          background: var(--ir-surface);
          border-radius: var(--ir-radius-lg);
          padding: var(--ir-space-lg);
        }
        .player-panel,
        .volume-panel,
        .history-panel,
        .favorites-panel { height: 100%; box-sizing: border-box; }
        .section-head {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: var(--ir-space-md);
          margin-bottom: var(--ir-space-md);
        }
        .title {
          font-size: 0.94rem;
          font-weight: 700;
          color: var(--ir-muted);
          margin: 0;
        }
        .tiny-version,
        .status-pill {
          font-size: 0.72rem;
          color: var(--ir-muted);
          white-space: nowrap;
        }
        .icon-btn,
        .round-btn,
        .arrow {
          width: 38px;
          height: 38px;
          border-radius: var(--ir-radius-pill);
          border: 1px solid var(--ir-border);
          background: var(--ir-surface-soft);
          color: var(--ir-text);
          display: inline-grid;
          place-items: center;
          font-size: 1.05rem;
          cursor: pointer;
          touch-action: manipulation;
        }
        .icon-btn:active,
        .round-btn:active,
        .arrow:active { transform: scale(0.96); }
        .icon-btn:disabled,
        .round-btn:disabled,
        .arrow:disabled {
          color: var(--ir-disabled);
          cursor: default;
          opacity: 0.55;
          transform: none;
        }
        .player-panel { display: grid; grid-template-columns: minmax(130px, 34%) minmax(0, 1fr); gap: var(--ir-space-lg); }
        .device-image-wrap {
          min-height: 160px;
          border-radius: var(--ir-radius-md);
          background: var(--ir-surface-soft);
          display: grid;
          place-items: center;
          overflow: hidden;
        }
        .device-image {
          width: 100%;
          height: 100%;
          max-height: 240px;
          object-fit: contain;
          display: block;
        }
        .now-playing { min-width: 0; display: flex; flex-direction: column; gap: var(--ir-space-md); }
        .now-label { color: var(--ir-muted); font-size: 0.78rem; font-weight: 700; text-transform: uppercase; }
        .station-row { display: flex; align-items: center; gap: var(--ir-space-md); min-width: 0; }
        .station-logo, .track-logo {
          width: 58px;
          height: 58px;
          flex: 0 0 58px;
          object-fit: cover;
          border-radius: var(--ir-radius-md);
          background: var(--ir-surface-soft);
        }
        .station-name {
          font-size: 1.3rem;
          line-height: 1.25;
          font-weight: 800;
          overflow-wrap: anywhere;
        }
        .track-box {
          background: var(--ir-surface-soft);
          border-radius: var(--ir-radius-md);
          padding: var(--ir-space-md);
          display: grid;
          grid-template-columns: auto minmax(0, 1fr);
          gap: var(--ir-space-md);
          align-items: center;
        }
        .track-title {
          font-size: 1.55rem;
          line-height: 1.18;
          font-weight: 800;
          overflow-wrap: anywhere;
        }
        .track-artist { color: var(--ir-muted); font-size: 1rem; margin-top: var(--ir-space-xs); overflow-wrap: anywhere; }
        .empty-note {
          color: var(--ir-muted);
          background: var(--ir-surface-soft);
          border-radius: var(--ir-radius-md);
          padding: var(--ir-space-lg);
          text-align: center;
        }
        .volume-panel { display: flex; flex-direction: column; justify-content: center; gap: var(--ir-space-md); }
        .control-strip {
          display: grid;
          grid-template-columns: auto auto minmax(110px, 1fr) auto auto auto;
          gap: var(--ir-space-sm);
          align-items: center;
          background: var(--ir-surface-soft);
          border-radius: var(--ir-radius-pill);
          padding: var(--ir-space-sm);
        }
        .vol-slider { width: 100%; min-width: 0; accent-color: var(--ir-accent); }
        .vol-current { color: var(--ir-text); font-size: 0.92rem; font-weight: 800; min-width: 42px; text-align: center; }
        .fav-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          grid-auto-rows: minmax(86px, auto);
          gap: var(--ir-space-sm);
        }
        .fav {
          position: relative;
          border: 1px solid transparent;
          border-radius: var(--ir-radius-md);
          background: var(--ir-surface-soft);
          display: grid;
          grid-template-columns: 66px minmax(0, 1fr);
          align-items: center;
          gap: var(--ir-space-sm);
          text-align: left;
          cursor: pointer;
          min-height: 86px;
          padding: var(--ir-space-sm);
          transition: transform .06s ease, border-color .12s ease, background .12s ease;
          touch-action: manipulation;
          -webkit-tap-highlight-color: transparent;
        }
        .fav:active { transform: scale(0.98); }
        .fav.active { border-color: var(--ir-accent); background: var(--ir-surface-strong); }
        .fav.active::after {
          content: "\\25B6";
          position: absolute;
          top: var(--ir-space-xs);
          right: var(--ir-space-xs);
          width: 24px;
          height: 24px;
          border-radius: var(--ir-radius-pill);
          display: grid;
          place-items: center;
          background: var(--ir-accent);
          color: var(--text-primary-color, var(--primary-background-color));
          font-size: 0.72rem;
        }
        .fav img { width: 66px; height: 66px; border-radius: var(--ir-radius-sm); object-fit: cover; background: var(--ir-surface); }
        .fav-placeholder {
          width: 66px;
          height: 66px;
          border-radius: var(--ir-radius-sm);
          display: grid;
          place-items: center;
          color: var(--ir-muted);
          background: var(--ir-surface);
          font-size: 0.74rem;
          font-weight: 800;
        }
        .fav-name {
          font-size: 0.9rem;
          line-height: 1.12rem;
          font-weight: 700;
          overflow: hidden;
          text-overflow: ellipsis;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
        }
        .pager-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: var(--ir-space-md);
          margin-top: var(--ir-space-md);
        }
        .pager { display: flex; align-items: center; justify-content: center; gap: var(--ir-space-sm); }
        .page-label { color: var(--ir-muted); font-size: 0.86rem; font-weight: 700; min-width: 90px; text-align: center; }
        .refresh-wrap { padding-left: var(--ir-space-md); border-left: 1px solid var(--ir-border); }
        .history {
          max-height: 285px;
          overflow: auto;
          display: grid;
          gap: var(--ir-space-xs);
          padding-right: var(--ir-space-xs);
        }
        .history-item {
          display: grid;
          grid-template-columns: 62px minmax(0, 1.2fr) minmax(0, 0.8fr) minmax(0, 0.8fr);
          gap: var(--ir-space-sm);
          align-items: center;
          min-height: 42px;
          padding: var(--ir-space-sm);
          border-radius: var(--ir-radius-sm);
          background: var(--ir-surface-soft);
        }
        .history-time { color: var(--ir-muted); font-size: 0.78rem; font-weight: 700; }
        .history-title { font-size: 0.9rem; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .history-artist,
        .history-station { color: var(--ir-muted); font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .missing { padding: var(--ir-space-lg); color: var(--ir-muted); }
        .touch-mode .fav { min-height: 92px; }
        .touch-mode .fav img,
        .touch-mode .fav-placeholder { width: 70px; height: 70px; }
        .touch-mode .icon-btn,
        .touch-mode .round-btn,
        .touch-mode .arrow { width: 42px; height: 42px; }
        @media (min-width: 1100px) {
          .grid { grid-template-columns: minmax(0, 1.08fr) minmax(0, 0.92fr); }
        }
        @media (min-width: 700px) and (max-width: 1099px) {
          .ir-card { padding: var(--ir-space-md); }
          .fill-screen { min-height: 760px; height: auto; }
          .grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-rows: auto auto; gap: var(--ir-space-md); }
          .player-panel { grid-template-columns: 112px minmax(0, 1fr); gap: var(--ir-space-md); }
          .device-image-wrap { min-height: 132px; }
          .track-title { font-size: 1.24rem; }
          .station-name { font-size: 1.08rem; }
          .control-strip { grid-template-columns: auto auto minmax(92px, 1fr) auto auto auto; }
          .fav-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          .history-item { grid-template-columns: 56px minmax(0, 1fr); }
          .history-artist,
          .history-station { grid-column: 2; }
        }
        @media (max-width: 699px) {
          .ir-card { padding: var(--ir-space-md); }
          .fill-screen,
          .fit-content { min-height: 0; height: auto; }
          .grid { grid-template-columns: 1fr; grid-template-rows: none; gap: var(--ir-space-md); }
          .player-panel { grid-template-columns: 1fr; }
          .device-image-wrap { min-height: 150px; }
          .track-title { font-size: 1.2rem; }
          .station-name { font-size: 1.08rem; }
          .control-strip { grid-template-columns: auto auto 1fr; border-radius: var(--ir-radius-lg); }
          .control-strip .vol-slider { grid-column: 1 / -1; grid-row: 2; }
          .fav-grid { grid-template-columns: 1fr; grid-auto-rows: minmax(76px, auto); }
          .fav { min-height: 76px; grid-template-columns: 58px minmax(0, 1fr); }
          .fav img,
          .fav-placeholder { width: 58px; height: 58px; }
          .pager-row { align-items: stretch; }
          .refresh-wrap { padding-left: var(--ir-space-sm); }
          .history { max-height: 260px; }
          .history-item { grid-template-columns: 52px minmax(0, 1fr); }
          .history-artist,
          .history-station { grid-column: 2; }
        }
      </style>
      <div class="${fillHeightClass} ${touchClass} ${themeClass}">
        <div class="grid">
          <section class="quad">
            <div class="player-panel">
              <div class="device-image-wrap">${
                cachedDeviceImageSrc
                  ? `<img class="device-image" src="${cachedDeviceImageSrc}" alt="Device" />`
                  : deviceImageCandidates.length
                  ? `<img class="device-image device-image-dyn" src="" data-srcs='${JSON.stringify(deviceImageCandidates)}' data-src-index="-1" alt="Device" />`
                  : deviceImageUrl
                    ? `<img class="device-image" src="${deviceImageUrl}" alt="Device" />`
                    : `<div class="empty-note">${isOff ? "Geraet ausgeschaltet" : isUnavailable ? "Nicht verfuegbar" : "Kein Geraetebild"}</div>`
              }</div>
              <div class="now-playing">
                <div class="section-head">
                  <div>
                    <div class="now-label">Jetzt laeuft</div>
                    <div class="tiny-version">Card v${CARD_VERSION}</div>
                  </div>
                  <div class="status-pill">${deviceState}</div>
                </div>
                <div class="station-row">
                  ${stationLogo ? `<img class="station-logo" src="${this._logoUrl(stationLogo)}" alt="Station" />` : ""}
                  <div class="station-name">${stationName}</div>
                </div>
                <div class="track-box">
                  ${trackLogo ? `<img class="track-logo" src="${this._logoUrl(trackLogo)}" alt="Track" />` : ""}
                  <div>
                    <div class="track-title">${trackTitle}</div>
                    ${trackArtist ? `<div class="track-artist">${trackArtist}</div>` : ""}
                  </div>
                </div>
              </div>
            </div>
          </section>
          <section class="quad">
            <div class="volume-panel">
              <div class="section-head">
                <h3 class="title">Lautstaerke</h3>
                <span class="status-pill">${volume}%</span>
              </div>
              <div class="control-strip">
                <button class="round-btn" id="vol-down" aria-label="Leiser">&minus;</button>
                <button class="round-btn" id="vol-up" aria-label="Lauter">+</button>
                <input type="range" id="vol-slider" class="vol-slider" min="0" max="100" value="50" aria-label="Lautstaerke" />
                <div class="vol-current" id="vol-current">${volume}%</div>
                <button class="round-btn" id="device-mute" title="Stumm schalten" aria-label="Stumm schalten">M</button>
                <button class="round-btn" id="device-off" title="Geraet ausschalten" aria-label="Geraet ausschalten">&#x23FB;</button>
              </div>
            </div>
          </section>
          <section class="quad">
            <div class="history-panel">
              <div class="section-head"><h3 class="title">Titelverlauf</h3><span class="status-pill">Neueste zuerst</span></div>
              <div class="history">
                ${renderedTrackHistory.length
                  ? renderedTrackHistory
                      .map(
                        (item) => `
                          <div class="history-item">
                            <div class="history-time">${this._formatPlayedAt(item.played_at)}</div>
                            <div class="history-title">${item.title || "Keine Titelinformationen"}</div>
                            <div class="history-artist">${item.artist || "Unbekannter Interpret"}</div>
                            <div class="history-station">${item.station_name || "Unbekannter Sender"}</div>
                          </div>
                        `
                      )
                      .join("")
                  : `<div class="empty-note">Noch kein Titelverlauf vorhanden</div>`}
              </div>
            </div>
          </section>
          <section class="quad">
            <div class="favorites-panel">
              <div class="section-head"><h3 class="title">Favoriten</h3><span class="status-pill">Seite ${this._page + 1} von ${totalPages}</span></div>
              <div class="fav-grid">
                ${visibleFavorites.length
                  ? visibleFavorites
                      .map((fav, idx) => {
                        const favNameKey = String(fav.name || "").trim().toLowerCase();
                        const activeClass = currentStationKey && favNameKey && favNameKey === currentStationKey ? " active" : "";
                        return `
                          <button class="fav${activeClass}" data-index="${start + idx}">
                            ${fav.logo ? `<img src="${this._logoUrl(fav.logo)}" alt="${fav.name || "Favorit"}" />` : `<div class="fav-placeholder">RADIO</div>`}
                            <div class="fav-name">${fav.name || "Unbenannt"}</div>
                          </button>
                        `;
                      })
                      .join("")
                  : `<div class="empty-note">Keine Favoriten vorhanden</div>`}
              </div>
              <div class="pager-row">
                <div class="pager">
                  <button class="arrow" id="prev" aria-label="Vorherige Seite" ${atFirstPage ? "disabled" : ""}>&#8592;</button>
                  <div class="page-label">Seite ${this._page + 1} von ${totalPages}</div>
                  <button class="arrow" id="next" aria-label="Naechste Seite" ${atLastPage ? "disabled" : ""}>&#8594;</button>
                </div>
                <div class="refresh-wrap"><button class="icon-btn" id="refresh-favorites" title="Favoriten aktualisieren" aria-label="Favoriten aktualisieren">&#8635;</button></div>
              </div>
            </div>
          </section>
        </div>
      </div>
    `;

    const slider = this.content.querySelector("#vol-slider");
    const volCurrent = this.content.querySelector("#vol-current");
    if (slider) slider.value = String(volume);
    if (volCurrent) volCurrent.textContent = `${volume}%`;

    this.content.querySelectorAll(".device-image-dyn").forEach((imgEl) => {
      const loadNext = async () => {
        let sources = [];
        try {
          sources = JSON.parse(imgEl.getAttribute("data-srcs") || "[]");
        } catch {
          sources = [];
        }
        const idx = Number(imgEl.getAttribute("data-src-index") || "-1");
        const next = idx + 1;
        if (next >= sources.length) return;
        imgEl.setAttribute("data-src-index", String(next));
        const src = sources[next];
        this._deviceImageDebug = {
          ...(this._deviceImageDebug || {}),
          currentSrc: src,
        };
        try {
          const ok = await this._tryLoadDeviceImageBlob(imgEl, src);
          if (!ok) throw new Error("blob_load_failed");
          const oldStateCache = this._deviceImageCache?.[deviceState];
          if (oldStateCache?.src && oldStateCache.src !== imgEl.src && oldStateCache.src.startsWith("blob:")) {
            URL.revokeObjectURL(oldStateCache.src);
          }
          this._deviceImageCache[deviceState] = {
            signature: sources.join("|"),
            src: imgEl.src,
          };
          this._deviceImageDebug = {
            ...(this._deviceImageDebug || {}),
            lastError: "",
          };
        } catch (err) {
          this._deviceImageDebug = {
            ...(this._deviceImageDebug || {}),
            lastError: `fetch_error:${src}::${err?.message || String(err)}`,
          };
          await loadNext();
        }
      };
      imgEl.addEventListener("error", () => {
        this._deviceImageDebug = {
          ...(this._deviceImageDebug || {}),
          lastError: `load_error:${imgEl.currentSrc || imgEl.src || "unknown"}`,
        };
        void loadNext();
      });
      void loadNext();
    });

    this.content.querySelectorAll(".fav").forEach((button) => {
      button.addEventListener("click", () => {
        const idx = Number(button.getAttribute("data-index"));
        this._hass.callService("internet_radio_ma", "play_favorite", {
          favorite_index: idx,
          entity_id: this._config.media_player,
        });
      });
    });

    this.content.querySelector("#prev")?.addEventListener("click", () => {
      if (this._page <= 0) return;
      this._page -= 1;
      this._render();
    });
    this.content.querySelector("#next")?.addEventListener("click", () => {
      if (this._page >= totalPages - 1) return;
      this._page += 1;
      this._render();
    });
    this.content.querySelector("#refresh-favorites")?.addEventListener("click", () => {
      this._hass.callService("internet_radio_ma", "refresh", {});
    });
    this.content.querySelector("#vol-up")?.addEventListener("click", () => {
      this._hass.callService("media_player", "volume_up", {
        entity_id: this._config.media_player,
      });
    });
    this.content.querySelector("#vol-down")?.addEventListener("click", () => {
      this._hass.callService("media_player", "volume_down", {
        entity_id: this._config.media_player,
      });
    });
    this.content.querySelector("#device-mute")?.addEventListener("click", () => {
      this._hass.callService("media_player", "volume_mute", {
        entity_id: this._config.media_player,
        is_volume_muted: true,
      });
    });
    slider?.addEventListener("change", () => {
      const val = Math.max(0, Math.min(100, Number(slider.value)));
      this._lastVolumePercent = val;
      if (volCurrent) volCurrent.textContent = `${val}%`;
      this._hass.callService("media_player", "volume_set", {
        entity_id: this._config.media_player,
        volume_level: val / 100,
      });
    });
    this.content.querySelector("#device-off")?.addEventListener("click", () => {
      this._hass.callService("media_player", "turn_off", {
        entity_id: this._config.media_player,
      });
    });
  }
}

class InternetRadioMiniNowPlayingCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement(MINI_EDITOR_TYPE);
  }

  static getStubConfig() {
    return {
      type: `custom:${MINI_CARD_TYPE}`,
      entity: "sensor.internet_radio_dashboard",
      media_player: "media_player.your_player",
      title: "Internetradio",
      off_hint: "hier Sender auswaehlen",
      navigation_path: "/",
    };
  }

  setConfig(config) {
    if (!config.media_player) throw new Error("media_player is required");
    this._config = {
      entity: "sensor.internet_radio_dashboard",
      title: "Internetradio",
      off_hint: "hier Sender auswaehlen",
      navigation_path: "",
      ...config,
    };
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return {
      rows: 1,
      min_rows: 1,
      max_rows: 1,
      columns: 12,
      min_columns: 12,
      max_columns: 12,
    };
  }

  _playerState() {
    return this._hass?.states?.[this._config?.media_player];
  }

  _dashboardState() {
    return this._hass?.states?.[this._config?.entity];
  }

  _entityPictureUrl(value) {
    if (!value || typeof value !== "string") return "";
    const raw = value.trim();
    if (!raw) return "";
    if (raw.startsWith("http://") || raw.startsWith("https://") || raw.startsWith("data:image/")) {
      return raw;
    }
    if (raw.startsWith("//")) return `${window.location.protocol}${raw}`;
    if (raw.startsWith("/")) return this._hass?.hassUrl ? this._hass.hassUrl(raw) : raw;
    return "";
  }

  _isQueueLike(value) {
    if (!value || typeof value !== "string") return false;
    return value.trim().toLowerCase().includes("music assistant queue");
  }

  _navigationTarget() {
    const selectedDashboard = this._config?.navigation_dashboard;
    if (typeof selectedDashboard === "string" && selectedDashboard.trim()) {
      return normalizeNavigationPath(selectedDashboard);
    }
    return normalizeNavigationPath(this._config?.navigation_path || "");
  }

  _navigate() {
    const target = this._navigationTarget();
    if (!target) return;
    if (target.startsWith("http://") || target.startsWith("https://")) {
      window.location.href = target;
      return;
    }
    window.history.pushState(null, "", target);
    fireEvent(window, "location-changed", { replace: false });
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.content) {
      this.innerHTML = "";
      this.content = document.createElement("ha-card");
      this.content.classList.add("ir-mini-card");
      this.appendChild(this.content);
    }

    const player = this._playerState();
    if (!player) {
      this.content.innerHTML = `<div class="missing">Entity ${this._config.media_player} not found</div>`;
      return;
    }

    const attrs = player.attributes || {};
    const dashboard = this._dashboardState();
    const dashboardAttrs = dashboard?.attributes || {};
    const station = dashboardAttrs.current_station || {};
    const track = dashboardAttrs.current_track || {};
    const dashboardDeviceState = String(dashboardAttrs.device_state || "").toLowerCase();
    const state = String(player.state || "off").toLowerCase();
    const isOff = dashboardDeviceState ? dashboardDeviceState === "off" : state === "off";
    const title = this._config.title || "Internetradio";
    const offHint = this._config.off_hint || "hier Sender auswaehlen";
    const stationName =
      station.name ||
      attrs.media_channel ||
      attrs.source ||
      "";
    const songTitle = track.title || attrs.media_title || attrs.title || "-";
    const artist = track.artist || attrs.media_artist || attrs.artist || "-";
    const stationLogo = this._entityPictureUrl(
      station.logo || attrs.entity_picture || attrs.entity_picture_local || attrs.media_image_url || ""
    );
    const showStationLine = !!stationName && !this._isQueueLike(stationName);
    const navTarget = this._navigationTarget();
    const tappableClass = navTarget ? "is-tappable" : "";

    this.content.innerHTML = `
      <style>
        .ir-mini-card {
          border-radius: 14px;
          overflow: hidden;
        }
        .mini-wrap {
          display: grid;
          grid-template-columns: auto 1fr;
          align-items: center;
          gap: 12px;
          min-height: 66px;
          padding: 10px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 14px;
          background: color-mix(in srgb, var(--card-background-color, var(--ha-card-background, var(--primary-background-color))) 92%, var(--primary-background-color));
          box-sizing: border-box;
        }
        .mini-wrap.is-tappable {
          cursor: pointer;
        }
        .mini-wrap.is-tappable:active {
          transform: scale(0.997);
        }
        .logo {
          width: 36px;
          height: 36px;
          border-radius: 9px;
          object-fit: cover;
          display: block;
          border: 1px solid color-mix(in srgb, var(--divider-color) 75%, transparent);
        }
        .logo-fallback {
          width: 36px;
          height: 36px;
          border-radius: 999px;
          border: 1px solid color-mix(in srgb, var(--divider-color) 75%, transparent);
          display: grid;
          place-items: center;
          color: var(--secondary-text-color);
        }
        .logo-fallback ha-icon {
          --mdc-icon-size: 20px;
        }
        .line-primary {
          font-size: 1rem;
          font-weight: 600;
          color: var(--primary-text-color);
          line-height: 1.2;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .line-secondary {
          margin-top: 2px;
          font-size: 0.96rem;
          color: var(--secondary-text-color);
          line-height: 1.2;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .line-tertiary {
          margin-top: 2px;
          font-size: 0.8rem;
          color: var(--secondary-text-color);
          line-height: 1.2;
          opacity: 0.9;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
      </style>
      <div class="mini-wrap ${tappableClass}" role="${navTarget ? "button" : "presentation"}" tabindex="${
      navTarget ? "0" : "-1"
    }">
        ${
          isOff || !stationLogo
            ? `<div class="logo-fallback"><ha-icon icon="mdi:radio-tower"></ha-icon></div>`
            : `<img class="logo" src="${stationLogo}" alt="${stationName}">`
        }
        <div class="text-block">
          ${
            isOff
              ? `<div class="line-primary">${title}</div><div class="line-secondary">${offHint}</div>`
              : `<div class="line-primary">${songTitle}</div><div class="line-secondary">${artist}</div>${
                  showStationLine ? `<div class="line-tertiary">${stationName}</div>` : ""
                }`
          }
        </div>
      </div>
    `;

    if (navTarget) {
      const wrap = this.content.querySelector(".mini-wrap");
      wrap?.addEventListener("click", () => this._navigate());
      wrap?.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          this._navigate();
        }
      });
    }
  }
}

class InternetRadioQuadCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...DEFAULT_CONFIG, ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _value(name) {
    return this._config?.[name];
  }

  _onInput(ev) {
    const updated = { ...this._config, ...(ev.detail?.value || {}) };
    if (updated.pageSize !== undefined) {
      updated.pageSize = Number(updated.pageSize || 9);
    }
    this._config = updated;
    fireEvent(this, "config-changed", { config: updated });
  }

  _render() {
    if (!this._hass) return;
    if (!this.content) {
      this.content = document.createElement("div");
      this.appendChild(this.content);
    }
    if (!this.formEl) {
      this.content.innerHTML = `
        <style>
          .editor { display: grid; gap: 10px; }
          .hint { font-size: 0.82rem; color: var(--secondary-text-color); }
        </style>
        <div class="editor"></div>
      `;
      const wrapper = this.content.querySelector(".editor");
      this.formEl = document.createElement("ha-form");
      this.formEl.addEventListener("value-changed", (ev) => this._onInput(ev));
      wrapper.appendChild(this.formEl);
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent =
        "Hinweis: Bild am besten per Medienauswahl setzen, alternativ Image-Entity oder URL als Fallback.";
      wrapper.appendChild(hint);
    }

    const schema = [
      { name: "entity", selector: { entity: { domain: "sensor" } } },
      { name: "media_player", selector: { entity: { domain: "media_player" } } },
      { name: "title", selector: { text: {} } },
      { name: "device_image_on_media", selector: { media: { accept: ["image/*"] } } },
      { name: "device_image_off_media", selector: { media: { accept: ["image/*"] } } },
      { name: "device_image_on_entity", selector: { entity: { domain: "image" } } },
      { name: "device_image_off_entity", selector: { entity: { domain: "image" } } },
      { name: "device_image_on", selector: { text: {} } },
      { name: "device_image_off", selector: { text: {} } },
      { name: "pageSize", selector: { number: { min: 3, max: 9, step: 1, mode: "box" } } },
      {
        name: "theme_mode",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "auto", label: "Vom Home Assistant Client" },
              { value: "light", label: "Light Theme" },
              { value: "dark", label: "Dark Theme" },
            ],
          },
        },
      },
      { name: "fill_screen", selector: { boolean: {} } },
      { name: "touch_mode", selector: { boolean: {} } },
    ];

    const labels = {
      entity: "Sensor Entity",
      media_player: "Media Player",
      title: "Titel",
      device_image_on_media: "Bild Geraet EIN (Medienauswahl)",
      device_image_off_media: "Bild Geraet AUS (Medienauswahl)",
      device_image_on_entity: "Bildquelle Geraet EIN (Image-Entity)",
      device_image_off_entity: "Bildquelle Geraet AUS (Image-Entity)",
      device_image_on: "Fallback Bild-URL Geraet EIN",
      device_image_off: "Fallback Bild-URL Geraet AUS",
      pageSize: "Favoriten pro Seite (max 9)",
      theme_mode: "Farbschema",
      fill_screen: "Vollbildhoehe verwenden",
      touch_mode: "Touch-Modus (groessere Buttons)",
    };

    this.formEl.hass = this._hass;
    this.formEl.schema = schema;
    this.formEl.data = { ...DEFAULT_CONFIG, ...this._config };
    this.formEl.computeLabel = (item) => labels[item.name] || item.name;
  }
}

class InternetRadioMiniNowPlayingCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: "sensor.internet_radio_dashboard",
      title: "Internetradio",
      off_hint: "hier Sender auswaehlen",
      navigation_path: "",
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _onInput(ev) {
    const updated = { ...this._config, ...(ev.detail?.value || {}) };
    if (updated.navigation_dashboard) {
      updated.navigation_path = "";
    }
    if (!updated.navigation_dashboard && !updated.navigation_path) {
      updated.navigation_path = "";
    }
    this._config = updated;
    fireEvent(this, "config-changed", { config: updated });
  }

  _dashboardOptions() {
    const options = this._dashboardOptionsCache || [];
    return [{ value: "", label: "Kein Ziel ausgewaehlt" }, ...options];
  }

  async _loadDashboardOptions() {
    if (!this._hass?.panels || !this._hass?.callWS) {
      this._dashboardOptionsCache = [];
      return;
    }

    const panels = Object.values(this._hass.panels).filter(
      (panel) => panel && panel.component_name === "lovelace"
    );
    const seen = new Set();
    const options = [];

    const makeDashboardPath = (panel) => {
      const rawPath =
        panel?.url_path ||
        panel?.config?.url_path ||
        panel?.config?.path ||
        (panel?.id === "lovelace" ? "lovelace" : "");
      return normalizeNavigationPath(rawPath);
    };

    const addOption = (value, label) => {
      const normalized = normalizeNavigationPath(value);
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      options.push({ value: normalized, label });
    };

    for (const panel of panels) {
      const dashboardPath = makeDashboardPath(panel);
      if (!dashboardPath) continue;
      const dashboardTitle =
        panel.title ||
        panel.config?.title ||
        panel.id ||
        dashboardPath.replace("/", "");

      addOption(dashboardPath, `${dashboardTitle} / (Startseite)`);

      try {
        const config = await this._hass.callWS({
          type: "lovelace/config",
          url_path: dashboardPath.replace(/^\//, ""),
        });
        const views = Array.isArray(config?.views) ? config.views : [];
        views.forEach((view, index) => {
          const viewPathRaw =
            typeof view?.path === "string" && view.path.trim()
              ? view.path.trim()
              : String(index);
          const viewPath = normalizeNavigationPath(`${dashboardPath}/${viewPathRaw}`);
          const viewTitle =
            (typeof view?.title === "string" && view.title.trim()) || `Ansicht ${index + 1}`;
          addOption(viewPath, `${dashboardTitle} / ${viewTitle}`);
        });
      } catch {
        // Keep base dashboard option even if view config is not readable.
      }
    }

    options.sort((a, b) => a.label.localeCompare(b.label, "de"));
    this._dashboardOptionsCache = options;
  }

  _ensureDashboardOptionsLoaded() {
    if (this._dashboardOptionsLoading) return;
    const panelSignature = JSON.stringify(
      Object.values(this._hass?.panels || {})
        .filter((panel) => panel?.component_name === "lovelace")
        .map((panel) => `${panel?.id || ""}|${panel?.url_path || ""}|${panel?.title || ""}`)
        .sort()
    );
    if (this._dashboardOptionsSignature === panelSignature && this._dashboardOptionsLoaded) return;
    this._dashboardOptionsLoading = true;
    this._dashboardOptionsSignature = panelSignature;
    void this._loadDashboardOptions().finally(() => {
      this._dashboardOptionsLoaded = true;
      this._dashboardOptionsLoading = false;
      this._render();
    });
  }

  _render() {
    if (!this._hass) return;
    this._ensureDashboardOptionsLoaded();
    if (!this.content) {
      this.content = document.createElement("div");
      this.appendChild(this.content);
    }
    if (!this.formEl) {
      this.content.innerHTML = `
        <style>
          .editor { display: grid; gap: 10px; }
          .hint { font-size: 0.82rem; color: var(--secondary-text-color); }
        </style>
        <div class="editor"></div>
      `;
      const wrapper = this.content.querySelector(".editor");
      this.formEl = document.createElement("ha-form");
      this.formEl.addEventListener("value-changed", (ev) => this._onInput(ev));
      wrapper.appendChild(this.formEl);
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent =
        "Aktion ist Tippen -> Navigieren. Ziel entweder per Dashboard-Auswahl oder per Pfad setzen.";
      wrapper.appendChild(hint);
    }

    const schema = [
      { name: "entity", selector: { entity: { domain: "sensor" } } },
      { name: "media_player", selector: { entity: { domain: "media_player" } } },
      { name: "title", selector: { text: {} } },
      { name: "off_hint", selector: { text: {} } },
      {
        name: "navigation_dashboard",
        selector: {
          select: {
            mode: "dropdown",
            options: this._dashboardOptions(),
          },
        },
      },
      { name: "navigation_path", selector: { text: {} } },
    ];

    const labels = {
      entity: "Dashboard Sensor Entity",
      media_player: "Media Player",
      title: "Titel bei AUS",
      off_hint: "Hinweistext bei AUS",
      navigation_dashboard: "Navigationsziel (Dashboard/Unterseite)",
      navigation_path: "Alternativer Navigationspfad (z.B. /lovelace-radio)",
    };

    this.formEl.hass = this._hass;
    this.formEl.schema = schema;
    this.formEl.data = { ...this._config };
    this.formEl.computeLabel = (item) => labels[item.name] || item.name;
  }
}

if (!customElements.get("internet-radio-quad-card")) {
  customElements.define("internet-radio-quad-card", InternetRadioQuadCard);
}
if (!customElements.get(EDITOR_TYPE)) {
  customElements.define(EDITOR_TYPE, InternetRadioQuadCardEditor);
}
if (!customElements.get(MINI_CARD_TYPE)) {
  customElements.define(MINI_CARD_TYPE, InternetRadioMiniNowPlayingCard);
}
if (!customElements.get(MINI_EDITOR_TYPE)) {
  customElements.define(MINI_EDITOR_TYPE, InternetRadioMiniNowPlayingCardEditor);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "Internet Radio Quad Card",
  description: "4-quadrant radio control and info dashboard",
});
window.customCards.push({
  type: MINI_CARD_TYPE,
  name: "Internet Radio Mini Now Playing Card",
  description: "Small one-line radio card with off hint and tap-to-navigate action",
});



