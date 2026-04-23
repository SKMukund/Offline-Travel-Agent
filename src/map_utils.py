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

import functools
import http.server
import re
import socket
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

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
_accumulated_layers: list[tuple[str, tuple, dict]] = []

# Persistent HTTP server shared by display_map_in_notebook and open_map_in_browser
_server: http.server.HTTPServer | None = None
_server_port: int = 0


# ── silent HTTP handler ───────────────────────────────────────────────────────

class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with all request logging suppressed.

    Without this, every 2-second version.txt poll produces a log line on
    stderr, flooding the terminal and Jupyter cell output.
    """
    def log_message(self, *_):
        pass


# ── local HTTP server ─────────────────────────────────────────────────────────

def _ensure_server(serve_dir: str) -> int:
    """Start the shared map HTTP server if not already running; return its port."""
    global _server, _server_port
    if _server is not None:
        return _server_port
    with socket.socket() as s:
        s.bind(("", 0))
        _server_port = s.getsockname()[1]
    handler = functools.partial(_SilentHandler, directory=serve_dir)
    _server = http.server.HTTPServer(("localhost", _server_port), handler)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _server_port


# ── CDN → local resource bundler ─────────────────────────────────────────────

# Only image extensions are downloaded from CSS url() references.
# Font files (.woff2/.ttf/etc.) are intentionally skipped — the map renders
# correctly without them; icon glyphs inside markers just fall back to text.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico"}


def _download_css_assets(css_path: Path, css_cdn_url: str, serve_root: Path) -> None:
    """
    Scan a downloaded CSS file for url() references that point to image files
    and download them to the path the local HTTP server will serve them from.

    Example: leaflet.awesome-markers.css contains
        url('images/markers-soft.png')
    The CSS is served at /static/leaflet.awesome-markers.css, so the browser
    resolves that to /static/images/markers-soft.png.  We therefore download
    the image from the CDN and store it at serve_root/static/images/markers-soft.png.
    """
    try:
        css_text = css_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    refs = re.findall(r'url\(["\']?([^)"\'?\s#][^)"\'?#\s]*)["\']?\)', css_text)

    for ref in refs:
        ref = ref.strip()
        if ref.startswith(("data:", "http://", "https://", "#")):
            continue
        ext = Path(ref.split("?")[0]).suffix.lower()
        if ext not in _IMAGE_EXTS:
            continue

        # Absolute CDN URL for this asset
        abs_url = urljoin(css_cdn_url, ref)

        # Compute where the browser will look for this file.
        # CSS is always saved to serve_root/static/<filename>.
        # Resolve the reference relative to "static/" using simple .. normalisation.
        css_serve_dir = "static"
        raw_parts = f"{css_serve_dir}/{ref}".split("/")
        normalised: list[str] = []
        for part in raw_parts:
            if part == "..":
                if normalised:
                    normalised.pop()
            elif part and part != ".":
                normalised.append(part)

        local_path = serve_root.joinpath(*normalised)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if not local_path.exists():
            try:
                urllib.request.urlretrieve(abs_url, local_path)
            except Exception:
                pass  # best-effort; missing images degrade gracefully


def _localize_resources(html_path: Path) -> None:
    """
    Download every CDN JS/CSS referenced in *html_path* into a sibling
    static/ directory, then rewrite the HTML to use those local copies.
    Also downloads image assets referenced inside the CSS files so that
    marker sprites render correctly without an internet connection.

    Files are cached — repeated calls cost only a filesystem stat per URL.
    """
    static_dir = html_path.parent / "static"
    static_dir.mkdir(exist_ok=True)

    html = html_path.read_text(encoding="utf-8")

    # Track original CDN URL alongside local path for CSS asset scanning
    downloaded_css: list[tuple[Path, str]] = []

    def _download(cdn_url: str) -> str | None:
        filename = cdn_url.split("/")[-1].split("?")[0]
        dest = static_dir / filename
        if not dest.exists():
            try:
                urllib.request.urlretrieve(cdn_url, dest)
            except Exception as exc:
                print(f"  [Map] could not download {cdn_url}: {exc}")
                return None
        if dest.suffix.lower() == ".css":
            downloaded_css.append((dest, cdn_url))
        return f"static/{filename}"

    def _replace_script(m: re.Match) -> str:
        local = _download(m.group(1))
        # Only replace opening tag; original </script> stays in place
        return f'<script src="{local}">' if local else m.group(0)

    def _replace_link(m: re.Match) -> str:
        local = _download(m.group(1))
        return f'<link rel="stylesheet" href="{local}"/>' if local else m.group(0)

    html = re.sub(r'<script src="(https?://[^"]+)"[^>]*>', _replace_script, html)
    html = re.sub(
        r'<link rel="stylesheet" href="(https?://[^"]+)"[^>]*/?>',
        _replace_link,
        html,
    )
    html_path.write_text(html, encoding="utf-8")

    # Download image assets referenced inside each CSS file
    for css_path, css_cdn_url in downloaded_css:
        _download_css_assets(css_path, css_cdn_url, html_path.parent)


# ── live-reload helpers ───────────────────────────────────────────────────────

# Polls version.txt every 2 s; reloads when the content changes.
_LIVE_RELOAD_JS = (
    "<script>"
    "(function(){"
    "var v=null;"
    "setInterval(function(){"
    "fetch('version.txt?t='+Date.now(),{cache:'no-store'})"
    ".then(function(r){return r.text();})"
    ".then(function(n){if(v===null){v=n;return;}if(n!==v){location.reload();}})"
    ".catch(function(){});"
    "},2000);"
    "})();"
    "</script>"
)


def _inject_live_reload(html_path: Path) -> None:
    """Inject the live-reload polling snippet and bump version.txt."""
    html = html_path.read_text(encoding="utf-8")
    if _LIVE_RELOAD_JS not in html:
        html = html.replace("</body>", _LIVE_RELOAD_JS + "\n</body>", 1)
        html_path.write_text(html, encoding="utf-8")
    (html_path.parent / "version.txt").write_text(str(time.time()), encoding="utf-8")


# ── internal builder ──────────────────────────────────────────────────────────

def _build_map(
    layers: list[tuple[str, tuple, dict]],
    tile_url: str | None,
) -> folium.Map:
    """Build a Folium map from accumulated layers, fit-bounded to all markers."""
    if not layers:
        return folium.Map(location=[20.0, 0.0], zoom_start=2)

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

    # Fit view tightly to markers so only the relevant city area is loaded
    if len(all_lats) > 1:
        pad = 0.003  # ~300 m padding
        m.fit_bounds(
            [
                [min(all_lats) - pad, min(all_lons) - pad],
                [max(all_lats) + pad, max(all_lons) + pad],
            ]
        )

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
    Build an interactive HTML map, save it, bundle CDN assets and marker
    images locally, and inject the live-reload snippet so an open browser
    tab auto-refreshes when the file changes.

    Returns (file:// URI of the saved HTML, the Folium map object).
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

    _localize_resources(out)   # download JS/CSS + marker images
    _inject_live_reload(out)   # inject polling JS + bump version.txt

    abs_path = out.resolve()
    print(f"  [Map saved → {abs_path}]")
    return abs_path.as_uri(), m


def clear_map(output_path: str = "map_output/map.html") -> str:
    """
    Write a blank world-view map, reset accumulated markers, and bump
    version.txt so an open browser tab reloads to the blank state.

    Returns the file:// URI of the cleared map file.
    """
    global _accumulated_layers
    _accumulated_layers = []
    blank = _build_map([], None)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    blank.save(str(out))
    _localize_resources(out)
    _inject_live_reload(out)
    return out.resolve().as_uri()


def open_map_in_browser(output_path: str = "map_output/map.html") -> None:
    """
    Open the map HTML in the system default browser via the local HTTP server.

    Accepts either a plain file path or a file:// URI (as returned by
    generate_map).  Subsequent generate_map calls bump version.txt and the
    live-reload JS in the open tab auto-refreshes — no second call needed.
    """
    import webbrowser

    clean = output_path.removeprefix("file://")
    out = Path(clean).resolve()
    port = _ensure_server(str(out.parent))
    url = f"http://localhost:{port}/{out.name}"
    webbrowser.open(url)
    print(f"  [Map opened in browser → {url}]")


def display_map_in_notebook(m: folium.Map, path: str | None = None) -> None:
    """
    Render the map inline in a Jupyter notebook via the persistent HTTP server.

    VS Code's notebook CSP blocks Leaflet's CDN resources when the map is
    embedded directly as HTML.  Serving via localhost avoids this restriction.
    The persistent server (started once, reused every turn) means the IFrame
    URL is stable and each new cell output fetches the latest saved map.html.

    Parameters
    ----------
    m    : Folium map object — used only when path is None (saves a fresh copy)
    path : file:// URI returned by generate_map — preferred path; skips re-save
    """
    try:
        from IPython.display import IFrame, display
    except ImportError:
        print("IPython not available — open the map HTML in a browser.")
        return

    if path is None:
        out = Path("map_output/map.html").resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(out))
        _localize_resources(out)
        _inject_live_reload(out)
    else:
        out = Path(path.removeprefix("file://")).resolve()

    port = _ensure_server(str(out.parent))
    display(IFrame(f"http://localhost:{port}/{out.name}", width="100%", height=500))
