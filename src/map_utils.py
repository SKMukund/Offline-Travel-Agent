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
_accumulated_layers: list[tuple[str, tuple, dict]] = []
# Reference kept so display_map_in_notebook() can use Folium's own repr
_last_map: folium.Map | None = None


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
    str   absolute path of the saved HTML file
    """
    global _accumulated_layers, _last_map

    if replace_existing:
        _accumulated_layers = [(area, coords, results)]
    else:
        _accumulated_layers.append((area, coords, results))

    _last_map = _build_map(_accumulated_layers, tile_url)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _last_map.save(str(out))
    print(f"  [Map saved → {out.resolve()}]")
    return str(out.resolve())


def clear_map(output_path: str = "map_output/map.html") -> None:
    """
    Overwrite the map with a blank world-view and reset accumulated markers.

    Called on agent exit so stale session markers are not visible if the
    map file is opened again after the program ends.
    """
    global _accumulated_layers, _last_map
    _accumulated_layers = []
    _last_map = _build_map([], None)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _last_map.save(str(out))


def display_map_in_notebook(width: int = 900, height: int = 520) -> None:
    """
    Render the last generated map inline inside a Jupyter / IPython notebook.

    Uses Folium's own _repr_html_() so the map is self-contained and
    correctly sized inside the cell — no external file serving needed.
    Safe to call even if IPython is unavailable (prints a fallback message).

    Parameters
    ----------
    width, height : pixel dimensions of the embedded map cell
    """
    try:
        from IPython.display import HTML, display
    except ImportError:
        print("IPython not available — open map_output/map.html in a browser.")
        return

    if _last_map is None:
        print("No map has been generated yet.")
        return

    # _repr_html_() returns a self-contained iframe srcdoc snippet that
    # Folium uses internally for notebook display — reliable cross-environment.
    html_snippet = _last_map._repr_html_()

    # Wrap in a sized div so the map fills a predictable area in the cell
    display(
        HTML(
            f'<div style="width:{width}px; height:{height}px;">'
            f"{html_snippet}"
            f"</div>"
        )
    )
