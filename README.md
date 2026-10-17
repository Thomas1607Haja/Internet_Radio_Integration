# Internet Radio MA Dashboard (Custom Integration)

Diese Custom Integration verbindet Home Assistant mit Music Assistant, um Internetradio-Favoriten anzuzeigen und direkt auf einem Ziel-Mediengeraet zu starten. Zusaetzlich wird eine Lovelace-Karte mit vier Quadranten bereitgestellt.

## Funktionen

- Liest Internetradio-Favoriten aus `music_assistant`.
- Startet Favoriten-Streams auf einem Ziel-`media_player`.
- Zeigt aktuellen Sender und aktuellen Titel.
- Fuehrt pro Sender eine Historie der letzten 10 erkannten Titel.
- Nutzt externe Quellen als Fallback fuer Logos:
  - Senderlogo: Radio Browser API
  - Titellogo: iTunes Search API
- Enthaltene Custom Card `internet-radio-quad-card` mit:
  - Oben links: Geraetebild + aktueller Sender/Titel
  - Oben rechts: Favoriten (3x3, mit Seiten)
  - Unten links: letzte 10 Titel
  - Unten rechts: Lautstaerke-Steuerung
  - Touch-optimiertes Layout mit grossen Schaltflaechen
  - Visueller Karten-Editor in Lovelace

## Installation

1. Kopiere den Ordner `custom_components/internet_radio_ma` nach `<config>/custom_components/`.
2. Starte Home Assistant neu.
3. Gehe zu `Einstellungen -> Geraete und Dienste -> Integration hinzufuegen` und suche nach `Internet Radio MA Dashboard`.
4. Waehle das Ziel-Mediengeraet und optional Bilder/Intervall.
5. Spaetere Anpassungen ueber `Einstellungen -> Geraete und Dienste -> Internet Radio MA Dashboard -> Konfigurieren`.

## Lovelace Karte

Die Karte wird als JS-Ressource automatisch registriert. Falls erforderlich, manuell als Ressource hinzufuegen:

- URL: `/local/internet_radio_ma/internet-radio-quad-card.js`
- Typ: `module`

Beispielkarte:

```yaml
type: custom:internet-radio-quad-card
entity: sensor.internet_radio_dashboard
media_player: media_player.bose_soundtouch_10
title: Internet Radio
```

Beispiel fuer die neue kleine Sender-/Now-Playing-Karte:

```yaml
type: custom:internet-radio-mini-nowplaying-card
media_player: media_player.bose_soundtouch_10
title: Internetradio
off_hint: hier Sender auswaehlen
navigation_dashboard: lovelace-radio
```

Hinweis zur Groesse in der Abschnitts-Ansicht (Sections): Die Mini-Karte ist auf `12 x 1` ausgelegt.

Empfehlung fuer Vollbild-Dashboard: eigene Ansicht mit nur dieser Karte verwenden.

Die Karte besitzt einen visuellen Editor in Lovelace (keine manuelle YAML-Bearbeitung erforderlich).

## Services

- `internet_radio_ma.play_favorite`
  - `entity_id` (optional)
  - `favorite_id` oder `favorite_index`
- `internet_radio_ma.refresh`

## Hinweise

- Die Titelhistorie wird ab Integrationsstart aufgebaut.
- Nicht jedes Mediengeraet liefert Sender-/Track-Metadaten gleich detailliert.
