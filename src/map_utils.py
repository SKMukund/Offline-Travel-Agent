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

import re
import urllib.request
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

_MAX_MARKERS_PER_CATEGORY = 100

# ── module-level state ────────────────────────────────────────────────────────
# Each entry: (area_name, (lat, lon), {category: DataFrame})
# The map object itself is NOT stored here — it is returned to the caller so
# that module reloads (e.g. %autoreload 2 in notebooks) cannot reset it.
_accumulated_layers: list[tuple[str, tuple, dict]] = []


# ── CDN → local resource bundler ─────────────────────────────────────────────

def _localize_resources(html_path: Path) -> None:
    """
    Download every CDN JS/CSS referenced in *html_path* into a sibling
    static/ directory, then rewrite the HTML to use those local copies.

    Only plain JS (<script src="...">) and CSS (<link ... href="...">) are
    handled; font files referenced inside CSS are left as-is.  Files are
    cached — repeated calls cost only a filesystem stat per URL.
    """
    static_dir = html_path.parent / "static"
    static_dir.mkdir(exist_ok=True)

    html = html_path.read_text(encoding="utf-8")

    def _download(url: str) -> str | None:
        filename = url.split("/")[-1].split("?")[0]
        dest = static_dir / filename
        if not dest.exists():
            try:
                urllib.request.urlretrieve(url, dest)
            except Exception as exc:
                print(f"  [Map] could not download {url}: {exc}")
                return None
        return f"static/{filename}"

    def _replace_script(m: re.Match) -> str:
        local = _download(m.group(1))
        # Only replace the opening tag; the original </script> stays in place
        return f'<script src="{local}">' if local else m.group(0)

    def _replace_link(m: re.Match) -> str:
        local = _download(m.group(1))
        return f'<link rel="stylesheet" href="{local}"/>' if local else m.group(0)

    # Match opening script tag only (closing </script> is left as-is)
    html = re.sub(r'<script src="(https?://[^"]+)"[^>]*>', _replace_script, html)
    html = re.sub(
        r'<link rel="stylesheet" href="(https?://[^"]+)"[^>]*/?>',
        _replace_link,
        html,
    )

    html_path.write_text(html, encoding="utf-8")


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
            if len(df) > _MAX_MARKERS_PER_CATEGORY:
                print(
                    f"  [Map] {category}: showing {_MAX_MARKERS_PER_CATEGORY}"
                    f" of {len(df)} results (closest first)"
                )
                df = df.head(_MAX_MARKERS_PER_CATEGORY)
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
) -> tuple[str, folium.Map]:
    """
    Build an interactive HTML map, save it, and bundle CDN assets locally.

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

    Returns
    -------
    tuple[str, folium.Map]
        (file:// URI of saved HTML file, the Folium map object)
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

    _localize_resources(out)

    abs_path = out.resolve()
    print(f"  [Map saved → {abs_path}]")
    return abs_path.as_uri(), m


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


def display_map_in_notebook(m: folium.Map, path: str | None = None) -> None:
    """
    Render a Folium map inline inside a Jupyter / IPython notebook.

    VS Code's notebook CSP blocks Leaflet's CDN resources when the map is
    embedded directly as HTML.  The workaround is to save the HTML file and
    serve it from a local HTTP server, then display it in an IFrame pointing
    to localhost — VS Code permits that origin.

    Parameters
    ----------
    m    : the Folium map object (used only when path is None and the file
           needs to be (re)saved to the default location)
    path : file:// URI or absolute path returned by generate_map.
           When None, saves to map_output/map.html.
    """
    try:
        from IPython.display import IFrame, display
    except ImportError:
        print("IPython not available — open the map HTML in a browser.")
        return

    import functools
    import http.server
    import socket
    import threading

    if path is None:
        out = Path("map_output/map.html").resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(out))
        _localize_resources(out)
    else:
        out = Path(path.removeprefix("file://")).resolve()

    # Spin up a local HTTP server so the browser can load static/ assets too
    with socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(out.parent),
    )
    server = http.server.HTTPServer(("localhost", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    display(IFrame(f"http://localhost:{port}/{out.name}", width="100%", height=500))
