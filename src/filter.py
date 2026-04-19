"""
filter.py — area/category detection and nearby-place filtering.

Public surface (unchanged from the previous version):
  detect_area(message)        → (area_name, coords) | (None, None)
  detect_categories(message)  → list[str]
  to_km(distance, unit)       → float
  filter_nearby(cache, coords, radius_km, categories, ...)  → {category: DataFrame}
  filter_data(message, cache, radius, unit)                 → (area, coords, results)

Breaking change vs. v1:
  filter_nearby and filter_data now accept a SpatialCache object (from cache.py)
  instead of raw DataFrames.  The agent is responsible for creating the cache
  once and passing it through.
"""

from __future__ import annotations

# ── area coordinates ────────────────────────────────────────────────────────

AREA_COORDINATES: dict[str, tuple[float, float]] = {
    # Manhattan
    "midtown":          (40.7549, -73.9840),
    "times square":     (40.7580, -73.9855),
    "manhattan":        (40.7831, -73.9712),
    "upper east side":  (40.7736, -73.9566),
    "upper west side":  (40.7870, -73.9754),
    "lower east side":  (40.7157, -73.9863),
    "harlem":           (40.8116, -73.9465),
    "soho":             (40.7233, -73.9973),
    "tribeca":          (40.7163, -74.0086),
    "financial district":(40.7074, -74.0113),
    "central park":     (40.7851, -73.9683),
    # Outer boroughs
    "brooklyn":         (40.6782, -73.9442),
    "queens":           (40.7282, -73.7949),
    "bronx":            (40.8448, -73.8648),
    "staten island":    (40.5795, -74.1502),
    # Brooklyn neighborhoods
    "williamsburg":     (40.7081, -73.9571),
    "dumbo":            (40.7033, -73.9881),
    "park slope":       (40.6710, -73.9814),
}

# ── category keywords ───────────────────────────────────────────────────────

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "restaurants":    ["eat", "food", "restaurant", "dinner", "lunch", "breakfast", "hungry", "dine"],
    "cafes":          ["coffee", "cafe", "tea", "latte", "espresso", "pasteries"],
    "bars":           ["bar", "drink", "drinks", "nightlife", "cocktail", "beer", "pub"],
    "hotels":         ["hotel", "stay", "sleep", "accommodation", "check in", "lodge"],
    "attractions":    ["visit", "see", "attraction", "sightseeing", "things to do", "tourist"],
    "museums":        ["museum", "art", "history", "exhibit", "gallery"],
    "hospitals":      ["hospital", "injury", "emergency", "urgent care", "sick", "doctor", "medical", "clinic"],
    "pharmacies":     ["pharmacy", "medicine", "prescription", "drugstore", "cvs", "walgreens"],
    "atms":           ["atm", "cash", "money", "withdraw", "bank"],
    "subway_stations":["subway", "metro", "train", "station", "transit", "underground"],
    "parks":          ["park", "outdoor", "walk", "nature", "garden", "green space"],
}

# ── unit conversion ─────────────────────────────────────────────────────────

def to_km(distance: float, unit: str = "km") -> float:
    """Convert *distance* in *unit* to kilometres."""
    return distance * 1.60934 if unit == "miles" else float(distance)


# ── detection helpers ───────────────────────────────────────────────────────

def detect_area(message: str) -> tuple[str | None, tuple | None]:
    """Return (area_name, (lat, lon)) for the first known area found in *message*."""
    msg = message.lower()
    for area, coords in AREA_COORDINATES.items():
        if area in msg:
            return area, coords
    return None, None


def detect_categories(message: str) -> list[str]:
    """Return every category whose keywords appear in *message*."""
    msg = message.lower()
    return [
        cat for cat, keywords in CATEGORY_KEYWORDS.items()
        if any(kw in msg for kw in keywords)
    ]


# ── nearby filtering (uses SpatialCache) ────────────────────────────────────

def filter_nearby(
    cache,
    coords: tuple[float, float],
    radius_km: float,
    categories: list[str],
    min_results: int = 5,
    max_expansions: int = 3,
    expansion_factor: float = 0.25,
) -> dict:
    """
    Query nearby places via *cache* with dynamic radius expansion.

    A ±25 % tolerance buffer is applied to the requested radius so the
    search is not overly rigid (places just at the boundary are included).
    If fewer than *min_results* total places are found after the buffered
    query, the search radius is expanded by *expansion_factor* (25 %) and
    retried — up to *max_expansions* times.

    Because SpatialCache keys results by exact radius, each expansion
    produces a distinct cache entry and repeated calls cost very little.

    Parameters
    ----------
    cache          : SpatialCache instance
    coords         : (lat, lon) search origin
    radius_km      : requested radius in kilometres (before buffer)
    categories     : category names to include
    min_results    : expand if total result count is below this threshold
    max_expansions : maximum number of radius-expansion retries
    expansion_factor: fractional radius increase per expansion (0.25 = 25 %)

    Returns
    -------
    dict  {category_name: DataFrame}  — DataFrames include distance_km /
          distance_miles columns, sorted ascending.
    """
    lat, lon = coords
    # Initial query uses a 25 % buffer around the user-requested radius.
    current_radius = radius_km * 1.25

    results = cache.query(lat, lon, current_radius, "km", categories)
    total = sum(len(df) for df in results.values())

    for _ in range(max_expansions):
        if total >= min_results:
            break
        current_radius *= (1.0 + expansion_factor)
        results = cache.query(lat, lon, current_radius, "km", categories)
        total = sum(len(df) for df in results.values())

    return results


# ── main filter entry point ─────────────────────────────────────────────────

def filter_data(
    message: str,
    cache,
    radius: float = 1.0,
    unit: str = "km",
) -> tuple[str | None, tuple | None, dict]:
    """
    Detect area and categories from *message*, then query *cache*.

    Returns
    -------
    (area_name, (lat, lon), {category: DataFrame})
    area_name and coords are None when no known area is mentioned.
    The result dict is empty when no categories are detected.
    """
    area, coords = detect_area(message)
    categories = detect_categories(message)

    if not area:
        return None, None, {}

    if not categories:
        return area, coords, {}

    radius_km = to_km(radius, unit)
    results = filter_nearby(cache, coords, radius_km, categories)
    # Cap each category at 20 results for the LLM context window
    results = {cat: df.head(20) for cat, df in results.items() if not df.empty}
    return area, coords, results
