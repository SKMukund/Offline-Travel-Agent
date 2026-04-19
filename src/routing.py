"""
routing.py — scaffold for future offline turn-by-turn directions.

This module is intentionally empty of implementation.  It exists to
establish the interface that the rest of the project (agent.py, future UI)
will call once routing is ready.
"""

from __future__ import annotations


def get_directions(
    start: tuple[float, float],
    end: tuple[float, float],
    graph,
) -> list[dict]:
    """
    [FUTURE] Compute turn-by-turn directions between two coordinates.

    Planned implementation
    ----------------------
    Dependencies: osmnx, networkx (both installable offline after first download)

    1. Snap origin and destination to the nearest road-network nodes:
         origin_node = osmnx.nearest_nodes(graph, start[1], start[0])
         dest_node   = osmnx.nearest_nodes(graph, end[1], end[0])

    2. Run Dijkstra's shortest path (built into NetworkX):
         route = networkx.shortest_path(
             graph, origin_node, dest_node, weight="length"
         )

    3. Convert the node sequence to a GeoDataFrame of road segments:
         route_gdf = osmnx.utils_graph.route_to_gdf(graph, route)

    4. Build and return a list of step dicts:
         [{"instruction": str, "distance_m": float, "lat": float, "lon": float}, ...]

    5. The `graph` parameter is a networkx.MultiDiGraph pre-downloaded via:
         graph = osmnx.graph_from_place("Manhattan, New York, USA", network_type="walk")
         osmnx.save_graphml(graph, "data/USA/New York/NYC/walk_network.graphml")
       and loaded offline later with:
         graph = osmnx.load_graphml("data/USA/New York/NYC/walk_network.graphml")

    Keeping the graph as an explicit parameter means routing stays
    independent of filter.py and cache.py — a future app can load the
    road network once and pass it around without coupling it to place data.

    Parameters
    ----------
    start : (lat, lon) of the journey origin
    end   : (lat, lon) of the destination
    graph : osmnx.MultiDiGraph representing the offline road network

    Returns
    -------
    list[dict]
        Ordered list of route steps, each with at minimum:
        {"instruction": str, "distance_m": float, "lat": float, "lon": float}

    Raises
    ------
    NotImplementedError
        Always — routing is not yet implemented.
    """
    raise NotImplementedError(
        "Routing is not yet implemented. "
        "See the docstring for the planned OSMnx + NetworkX approach."
    )
