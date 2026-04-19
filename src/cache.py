"""
SpatialCache — fast spatial lookup + result memoisation for nearby-place queries.

Why KDTree (scipy.spatial) over alternatives:
  - R-tree (rtree/shapely): requires libspatialindex C bindings; heavier install.
  - Ball-tree (sklearn): sklearn is a large dependency for a single utility.
  - KDTree: ships with scipy (already a common geospatial dep), pure-Python
    fallback available, fully offline, and accurate enough after the geodesic
    post-filter step that corrects the flat-earth approximation.
"""

from __future__ import annotations

import numpy as np
from geopy.distance import geodesic
from scipy.spatial import KDTree


class SpatialCache:
    """Spatial index + query-result cache built from a loaded data dict."""

    # 1 degree of latitude ≈ 111 km (constant).
    # Longitude varies with cos(lat); the buffer below absorbs that error.
    _KM_PER_DEG: float = 111.0
    # Over-extend the KDTree radius by this factor so no real candidate is clipped
    # by the flat-earth approximation before the precise geodesic check.
    _CANDIDATE_BUFFER: float = 1.5

    def __init__(self, data: dict) -> None:
        """
        Parameters
        ----------
        data : dict
            {category_name: DataFrame} with at least 'latitude' and
            'longitude' columns.  None values and empty DataFrames are skipped.
        """
        # Work on a shallow copy so we can reset_index without mutating caller data
        self._data: dict = {}
        self._trees: dict[str, KDTree] = {}
        self._coord_arrays: dict[str, np.ndarray] = {}
        self._cache: dict[tuple, dict] = {}

        self._build_indices(data)

    # ── index construction ──────────────────────────────────────────────────

    def _build_indices(self, data: dict) -> None:
        for category, df in data.items():
            if df is None or df.empty:
                continue
            df = df.reset_index(drop=True)
            self._data[category] = df
            coords = df[["latitude", "longitude"]].to_numpy(dtype=float)
            self._coord_arrays[category] = coords
            self._trees[category] = KDTree(coords)

    # ── public API ──────────────────────────────────────────────────────────

    def query(
        self,
        lat: float,
        lon: float,
        radius: float,
        unit: str,
        categories: list[str],
    ) -> dict:
        """
        Return {category: DataFrame} for all places within *radius* of (lat, lon).

        Steps:
          1. Convert radius to km.
          2. Compute a degree-space bounding radius (with buffer) for KDTree.
          3. Retrieve candidate indices cheaply via KDTree.
          4. Apply exact geodesic filter on the small candidate set.
          5. Memoize by (lat, lon, radius_km, categories) so repeated queries
             with the same parameters are free.

        Parameters
        ----------
        lat, lon   : search origin in decimal degrees
        radius     : search distance in *unit*
        unit       : "km" or "miles"
        categories : list of category names to include
        """
        radius_km = radius * 1.60934 if unit == "miles" else float(radius)
        cache_key = (
            round(lat, 5),
            round(lon, 5),
            round(radius_km, 4),
            tuple(sorted(categories)),
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Degree radius for KDTree pre-filter (generous to avoid false negatives)
        deg_radius = (radius_km / self._KM_PER_DEG) * self._CANDIDATE_BUFFER
        results: dict = {}

        for category in categories:
            df = self._data.get(category)
            tree = self._trees.get(category)
            if df is None or tree is None:
                continue

            candidate_idxs: list[int] = tree.query_ball_point(
                [lat, lon], deg_radius
            )
            if not candidate_idxs:
                results[category] = df.iloc[[]]  # empty slice, preserves columns
                continue

            candidates = df.iloc[candidate_idxs].copy()
            candidates["distance_km"] = candidates.apply(
                lambda row: geodesic(
                    (lat, lon), (row["latitude"], row["longitude"])
                ).km,
                axis=1,
            )
            candidates["distance_miles"] = candidates["distance_km"] / 1.60934
            nearby = candidates[candidates["distance_km"] <= radius_km].sort_values(
                "distance_km"
            )
            results[category] = nearby

        self._cache[cache_key] = results
        return results

    def clear_cache(self) -> None:
        """Discard all memoized query results. Spatial indices are preserved."""
        self._cache.clear()


# ── future transit support ──────────────────────────────────────────────────

def load_transit_data(city: str) -> dict:
    """
    [FUTURE] Load offline GTFS transit data for *city*.

    Planned implementation
    ----------------------
    1. Accept a city name or path to a local GTFS .zip file.
    2. Parse the standard GTFS tables:
         stops.txt        → DataFrame(stop_id, stop_name, lat, lon)
         routes.txt       → DataFrame(route_id, route_short_name, route_type)
         trips.txt        → DataFrame(trip_id, route_id, service_id)
         stop_times.txt   → DataFrame(trip_id, stop_id, arrival_time, ...)
         calendar.txt     → DataFrame(service_id, days, start_date, end_date)
    3. Return a dict:
         {
           "stops":  DataFrame,   # can be indexed by SpatialCache for nearby stops
           "routes": DataFrame,
           "trips":  DataFrame,
         }
    4. The stops DataFrame is compatible with SpatialCache so users can query
       "nearest subway stop" the same way they query restaurants.
    5. The route/trip data feeds into src/routing.py for transit-aware directions.

    Free GTFS feeds
    ---------------
    - MTA (NYC):  https://api.mta.info/#/subwayRealtime (static feed zip)
    - Worldwide:  https://database.mobilitydata.org/

    Parameters
    ----------
    city : city name or path to a GTFS zip file

    Returns
    -------
    dict
        Structure described above (not yet implemented).

    Raises
    ------
    NotImplementedError
        Always — GTFS support is not yet implemented.
    """
    raise NotImplementedError(
        f"GTFS transit data loading is not yet implemented for '{city}'. "
        "See the docstring for the planned data structure and feed sources."
    )
