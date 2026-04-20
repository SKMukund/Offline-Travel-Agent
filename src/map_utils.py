"""
map_utils.py — offline-friendly interactive map generation via Folium.

Tile note
---------
Folium controls the map's initial center and zoom bounds, but tile *images*
are fetched by Leaflet.js in the browser as the user pans/zooms. Folium
cannot crop which tiles are downloaded — only a local tile server (e.g.
tileserver-gl serving an .mbtiles file) can make that fully offline.
The tile_url parameter is the only thing to change when switching to a
local tile server; all marker/bounds logic stays the same.
"""

from __future__ import annotations

from pathlib import Path

import folium

# ── category → marker colour ─────────────────────────────────────────────────
_CATEGORY_COLORS: dict[str, str] = {
    "restaurants":     "orange",
    "cafes":           "beige",
    "bars":            "purple",
    "hotels":          "blue",
    "attractions":     "green",
    "museums":         "darkblue",
    "parks":           "darkgreen",
    "pharmacies":      "pink",
    "atms":            "lightgray",
    "hospitals":       "red",
    "subway_stations": "cadetblue",
}
_DEFAULT_COLOR = "blue"

# ── module-level state ────────────────────────────────────────────────────────
# Each entry: (area_name, (lat, lon), {category: DataFrame})
# _accumulated_layers tracks history for optional replace_existing=False mode.
# The map object itself is NOT stored here — it is returned to the caller so
# that module reloads (e.g. %autoreload 2 in notebooks) cannot reset it.
_accumulated_layers: list[tuple[str, tuple, dict]] = []


# ── internal builder ──────────────────────────────────────────────────────────

def _build_map(
    layers: list[tuple[str, tuple, dict]],
    tile_url: str | None,
) -> folium.Map:
    """
    Render a Folium map from a list of (area, origin_coords, results) layers.

    The map is automatically fit-bounded to all markers so only the relevant
    region is shown in the initial view. If there is only one point the map
    just centres on it at zoom 15.
    """
    if not layers:
        # Blank world-view map — shown after the session is cleared
        return folium.Map(location=[20.0, 0.0], zoom_start=2)

    # ── collect all coordinates for bounds and centroid ──────────────
    all_lats: list[float] = []
    all_lons: list[float] = []
    for _, (lat, lon), results in layers:
        all_lats.append(lat)
        all_lons.append(lon)
        for df in results.values():
            if df is not None and not df.empty:
                all_lats.extend(df["latitude"].tolist())
                all_lons.extend(df["longitude"].tolist())

    center_lat = sum(all_lats) / len(all_lats)
    center_lon = sum(all_lons) / len(all_lons)

    if tile_url:
        m = folium.Map(location=[center_lat, center_lon], zoom_start=15, tiles=None)
        folium.TileLayer(
            tiles=tile_url,
            attr="Local tile server",
            name="Offline Tiles",
        ).add_to(m)
    else:
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=15,
            tiles="OpenStreetMap",
        )

    # Fit view to all markers so only the needed region is visible
    if len(all_lats) > 1:
        pad = 0.003  # ~300 m padding around the bounding box
        m.fit_bounds(
            [
                [min(all_lats) - pad, min(all_lons) - pad],
                [max(all_lats) + pad, max(all_lons) + pad],
            ]
        )

    # ── render markers ────────────────────────────────────────────────
    seen_origins: set[tuple] = set()
    for area, (lat, lon), results in layers:
        origin_key = (round(lat, 5), round(lon, 5))
        if origin_key not in seen_origins:
            seen_origins.add(origin_key)
            folium.Marker(
                location=[lat, lon],
                popup=folium.Popup(
                    f"<b>You are here</b><br>{area.title()}", max_width=200
                ),
                tooltip="You are here",
                icon=folium.Icon(color="red", icon="home", prefix="fa"),
            ).add_to(m)

        for category, df in results.items():
            if df is None or df.empty:
                continue
            color = _CATEGORY_COLORS.get(category, _DEFAULT_COLOR)
            for _, row in df.iterrows():
                name = row.get("name", "Unknown")
                address = row.get("addr:street", "")
                dist_km = row.get("distance_km", float("nan"))
                dist_mi = row.get("distance_miles", float("nan"))
                place_lat = row["latitude"]
                place_lon = row["longitude"]

                addr_line = f"<br>Address: {address}" if address else ""
                popup_html = (
                    f"<b>{name}</b><br>"
                    f"<i>{category.replace('_', ' ').title()}</i>"
                    f"{addr_line}<br>"
                    f"Lat: {place_lat:.5f}, Lon: {place_lon:.5f}<br>"
                    f"Distance: {dist_km:.2f} km / {dist_mi:.2f} mi"
                )
                folium.Marker(
                    location=[place_lat, place_lon],
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=name,
                    icon=folium.Icon(color=color),
                ).add_to(m)

    return m


# ── public API ────────────────────────────────────────────────────────────────

def generate_map(
    area: str,
    coords: tuple[float, float],
    results: dict,
    output_path: str = "map_output/map.html",
    tile_url: str | None = None,
    replace_existing: bool = True,
) -> str:
    """
    Build an interactive HTML map and save it to output_path.

    Parameters
    ----------
    area             : neighbourhood name shown on the origin marker
    coords           : (lat, lon) of the search origin
    results          : {category: DataFrame} from filter_nearby
    output_path      : destination HTML file (directories created automatically)
    tile_url         : custom tile URL for a local tile server, e.g.
                       "http://localhost:8080/{z}/{x}/{y}.png"
                       Leave None to use OpenStreetMap tiles (requires internet).
    replace_existing : True  — clear previous markers; show only this turn's results.
                       False — keep previous markers and append new ones.
                       Future natural-language support ("keep the old spots too")
                       should set this flag to False before calling.

    Returns
    -------
    tuple[str, folium.Map]
        (absolute path of saved HTML file, the Folium map object)
        The map object is returned to the caller so it can be held in a
        longer-lived scope (e.g. run_travel_agent's local variable) rather
        than relying on module-level state that a module reload would reset.
    """
    global _accumulated_layers

    if replace_existing:
        _accumulated_layers = [(area, coords, results)]
    else:
        _accumulated_layers.append((area, coords, results))

    m = _build_map(_accumulated_layers, tile_url)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out))
    print(f"  [Map saved → {out.resolve()}]")
    return str(out.resolve()), m


def clear_map(output_path: str = "map_output/map.html") -> None:
    """
    Overwrite the map with a blank world-view and reset accumulated markers.

    Called on agent exit so stale session markers are not visible if the
    map file is opened again after the program ends.
    """
    global _accumulated_layers
    _accumulated_layers = []
    blank = _build_map([], None)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    blank.save(str(out))


def display_map_in_notebook(m: folium.Map) -> None:
    """
    Render a Folium map inline inside a Jupyter / IPython notebook.

    Takes the map object explicitly so the caller (agent.py) holds the
    reference in its own scope — this survives module reloads (%autoreload).
    Delegates to Folium's own display path so sizing and JS are correct.
    Safe to call when IPython is unavailable (prints a fallback message).
    """
    try:
        from IPython.display import display
    except ImportError:
        print("IPython not available — open map_output/map.html in a browser.")
        return

    # Folium's __repr_html__ renders a self-sizing iframe — most reliable path
    display(m)
