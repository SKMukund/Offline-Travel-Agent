"""
main.py — end-to-end smoke test + interactive agent launcher.

Run from the project root:
    python main.py

Steps:
  1. Load NYC data via data_loader
  2. Build SpatialCache and run a sample spatial query
  3. Exercise filter helpers (detect_area, detect_categories, filter_data)
  4. Generate and save a test map, then clear it
  5. Confirm routing scaffold raises NotImplementedError
  6. Launch the interactive travel agent
"""

import sys
import os

# Make src/ importable regardless of working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from data_loader import load_city_data
from cache import SpatialCache
from filter import detect_area, detect_categories, filter_data, to_km
from map_utils import generate_map, clear_map
from routing import get_directions
from agent import run_travel_agent

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
STEP = "\033[94m[STEP]\033[0m"


def check(label: str, condition: bool) -> bool:
    print(f"  {'✓' if condition else '✗'} {label} →{PASS if condition else FAIL}")
    return condition


def section(title: str) -> None:
    print(f"\n{STEP} {title}")
    print("  " + "─" * 56)


# ─────────────────────────────────────────────────────────────
# STEP 1 — Data loader
# ─────────────────────────────────────────────────────────────
section("1 / 5  Load NYC data")
data = load_city_data("USA", "NYC", "New York")

check("data is a non-empty dict", isinstance(data, dict) and len(data) > 0)
check("restaurants loaded", "restaurants" in data and not data["restaurants"].empty)
check(
    "latitude / longitude columns present",
    "latitude" in data["restaurants"].columns
    and "longitude" in data["restaurants"].columns,
)
print(f"  → {len(data)} categories loaded: {', '.join(data.keys())}")
print(f"  → restaurants: {len(data['restaurants'])} rows")


# ─────────────────────────────────────────────────────────────
# STEP 2 — SpatialCache + KDTree query
# ─────────────────────────────────────────────────────────────
section("2 / 5  SpatialCache — build indices + query")
cache = SpatialCache(data)

midtown_lat, midtown_lon = 40.7549, -73.9840
results = cache.query(midtown_lat, midtown_lon, radius=1.0, unit="km", categories=["restaurants"])

check("cache returns a dict", isinstance(results, dict))
check("restaurants key present", "restaurants" in results)
check(
    "results have distance_km column",
    not results["restaurants"].empty
    and "distance_km" in results["restaurants"].columns,
)
check(
    "results sorted ascending by distance",
    list(results["restaurants"]["distance_km"])
    == sorted(results["restaurants"]["distance_km"]),
)

# Cache hit — second call should return the same object
results2 = cache.query(midtown_lat, midtown_lon, radius=1.0, unit="km", categories=["restaurants"])
check("memoisation: second call returns same dict", results is results2)

n = len(results["restaurants"])
print(f"  → {n} restaurant(s) within 1 km of Midtown")
if n > 0:
    top = results["restaurants"].iloc[0]
    print(f"  → nearest: {top['name']}  ({top['distance_km']:.2f} km)")


# ─────────────────────────────────────────────────────────────
# STEP 3 — Filter helpers
# ─────────────────────────────────────────────────────────────
section("3 / 5  Filter — area detection, category detection, filter_data")

area, coords = detect_area("I'm near midtown and want food")
check("detect_area finds 'midtown'", area == "midtown")
check("detect_area returns coords tuple", isinstance(coords, tuple) and len(coords) == 2)

cats = detect_categories("looking for coffee and a bar")
check("detect_categories finds 'cafes'", "cafes" in cats)
check("detect_categories finds 'bars'", "bars" in cats)

cats_none = detect_categories("hello there")
check("detect_categories returns [] for unrelated text", cats_none == [])

area_fd, coords_fd, res_fd = filter_data(
    "restaurants near midtown", cache, radius=1.0, unit="km"
)
check("filter_data returns correct area", area_fd == "midtown")
check("filter_data returns results dict", isinstance(res_fd, dict))
check("filter_data results non-empty", len(res_fd) > 0)

km = to_km(1.0, "miles")
check(f"to_km(1.0, miles) ≈ 1.609", abs(km - 1.60934) < 0.001)


# ─────────────────────────────────────────────────────────────
# STEP 4 — Map generation + clear
# ─────────────────────────────────────────────────────────────
section("4 / 5  Map — generate, fit-bounds, clear")
map_path, _ = generate_map(
    area="midtown",
    coords=(midtown_lat, midtown_lon),
    results=res_fd,
    output_path="map_output/map.html",
    replace_existing=True,
)

from pathlib import Path
map_file = Path(map_path)
check("map HTML file created", map_file.exists())
check("map file is non-trivial (> 10 KB)", map_file.stat().st_size > 10_000)

# Verify fit_bounds is in the HTML (confirms bounded region logic ran)
html_content = map_file.read_text(encoding="utf-8")
check("fit_bounds written to HTML", "fitBounds" in html_content)
check("'You are here' marker present", "You are here" in html_content)

clear_map("map_output/map.html")
cleared_content = map_file.read_text(encoding="utf-8")
check("clear_map removes 'You are here' marker", "You are here" not in cleared_content)
print(f"  → map saved to: {map_path}")


# ─────────────────────────────────────────────────────────────
# STEP 5 — Routing scaffold
# ─────────────────────────────────────────────────────────────
section("5 / 5  Routing scaffold — NotImplementedError")
try:
    get_directions((0, 0), (1, 1), graph=None)
    check("raises NotImplementedError", False)
except NotImplementedError:
    check("raises NotImplementedError as expected", True)


# ─────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  All smoke tests done. Launching interactive agent...")
print("=" * 60)
print("  Type your location + what you're looking for.")
print("  e.g. 'restaurants near midtown'")
print("  Type 'quit' to exit.\n")

run_travel_agent(data, radius=1.0, unit="km", location=("USA", "NYC", "New York"))
