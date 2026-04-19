# ─────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────
from __future__ import annotations

import os
import re
import sys

import ollama

try:
    from IPython.display import clear_output
    _IN_NOTEBOOK = True
except ImportError:
    _IN_NOTEBOOK = False

# Ensure src/ is on the path so this module works whether imported from the
# project root (e.g. a notebook) or run directly from within src/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cache import SpatialCache
from filter import detect_categories, filter_data, filter_nearby, to_km
from map_utils import clear_map, display_map_in_notebook, generate_map

MODEL = "llama3.2"


# ─────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────
SYSTEM_PROMPT = """\
You are an offline travel agent. You help users find places to visit, eat, stay, and explore.

STRICT RULES — never break these:
1. Only recommend places that appear in the [LOCAL DATA] blocks provided in this conversation.
2. Never suggest places from your own training knowledge. If a place is not in the data, say so.
3. When data is provided, always give specific names and distances from the data.
4. If you do not know the user's area or what they are looking for, ask a follow-up question.
5. Keep responses concise and focused on helping the user choose from the available options.\
"""


# ─────────────────────────────────────────
# OLLAMA HELPER
# ─────────────────────────────────────────
def ask_ollama(conversation_history: list[dict]) -> str:
    """Send the full conversation history to Ollama and return the reply."""
    response = ollama.chat(model=MODEL, messages=conversation_history)
    return response["message"]["content"]


# ─────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────
def _detect_radius(message: str) -> tuple[float | None, str | None]:
    """Extract radius and unit from natural language, e.g. '2 km' or '1.5 miles'."""
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(km|mile|miles|mi)\b", message, re.IGNORECASE
    )
    if match:
        radius = float(match.group(1))
        unit = "miles" if match.group(2).lower() in ("mile", "miles", "mi") else "km"
        return radius, unit
    return None, None


def _print_history(conversation_history: list[dict]) -> None:
    """Clear screen and reprint the full chat in Agent/User format."""
    if _IN_NOTEBOOK:
        clear_output(wait=True)
    print("=" * 60)
    print("  Offline Travel Agent  (powered by llama3.2 via Ollama)")
    print("=" * 60 + "\n")
    for msg in conversation_history:
        if msg["role"] == "system":
            continue
        # Strip injected LOCAL DATA blocks so the user sees their clean message
        display_content = msg["content"].split("\n\n[LOCAL DATA")[0].strip()
        if msg["role"] == "user":
            print(f'User: "{display_content}"')
        else:
            print(f'Agent: "{display_content}"')
    print()


def _results_to_context(area: str, results: dict) -> str:
    """Format filtered DataFrames into a plain-text context block for Ollama."""
    lines = [f"Places near {area.title()}:"]
    for category, df in results.items():
        if df.empty:
            continue
        lines.append(f"\n{category.upper()}:")
        for _, row in df.iterrows():
            dist_km = row["distance_km"]
            dist_mi = row["distance_miles"]
            lines.append(
                f"  - {row['name']}  ({dist_km:.2f} km / {dist_mi:.2f} mi away)"
            )
    return "\n".join(lines)


# ─────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────
def run_travel_agent(
    data: dict,
    radius: float = 1.0,
    unit: str = "km",
    location: tuple | None = None,
) -> None:
    """
    Start the interactive travel agent conversation loop.

    Parameters
    ----------
    data     : {category: DataFrame} from data_loader.load_city_data()
    radius   : default search radius
    unit     : default unit — "km" or "miles"
    location : optional (country, city) or (country, city, state) tuple
               used for display in the greeting, e.g. ("USA", "NYC", "New York")

    The agent builds a SpatialCache from *data* once at startup; all
    nearby-place lookups go through the cache for the lifetime of the
    session.  Radius changes from natural language ("within 2 miles")
    are picked up automatically each turn.
    """
    # ── one-time setup ──────────────────────────────────────────────
    cache = SpatialCache(data)

    conversation_history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    current_radius = radius
    current_unit = unit
    current_area: str | None = None
    current_coords: tuple | None = None

    # Build a human-readable city label from the optional location tuple
    if location:
        city_label = ", ".join(str(p) for p in location if p)
    else:
        city_label = "your city"

    greeting = (
        f"Hi! I'm your offline travel agent for {city_label}. "
        "I can help you find restaurants, cafes, bars, hotels, "
        "attractions, museums, parks, and more — all offline. "
        "Tell me where you are and what you're looking for. "
        "(Type 'quit' to exit, or include '2 km' / '1.5 miles' to change the search radius.)"
    )
    conversation_history.append({"role": "assistant", "content": greeting})
    _print_history(conversation_history)

    # ── conversation loop ───────────────────────────────────────────
    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            conversation_history.append(
                {"role": "assistant", "content": "Goodbye! Safe travels!"}
            )
            _print_history(conversation_history)
            clear_map()
            break

        if user_input.lower() in ("quit", "exit", "q"):
            conversation_history.append(
                {"role": "assistant", "content": "Goodbye! Safe travels!"}
            )
            _print_history(conversation_history)
            clear_map()
            break

        if not user_input:
            continue

        _map_path: str | None = None

        # ── radius update from natural language ─────────────────────
        new_radius, new_unit = _detect_radius(user_input)
        if new_radius is not None:
            current_radius = new_radius
            current_unit = new_unit
            print(f"  [Search radius updated to {current_radius} {current_unit}]")

        # ── area + category detection and cache query ────────────────
        area, coords, results = filter_data(
            user_input, cache, current_radius, current_unit
        )

        # Persist area/coords so the user doesn't repeat their location
        if area:
            current_area = area
            current_coords = coords
        elif current_area and current_coords:
            # Area not in this message — fall back to stored location and
            # query newly mentioned categories through the cache.
            categories = detect_categories(user_input)
            if categories:
                area = current_area
                radius_km = to_km(current_radius, current_unit)
                results = filter_nearby(
                    cache, current_coords, radius_km, categories
                )
                results = {cat: df.head(20) for cat, df in results.items() if not df.empty}

        # ── build injected content for conversation history ──────────
        if area and results:
            total = sum(len(df) for df in results.values())
            cats_found = list(results.keys())
            print(
                f"  [Area: {area.title()} | "
                f"{total} result(s) in: {', '.join(cats_found)}]"
            )
            context_block = _results_to_context(area, results)
            injected_content = (
                f"{user_input}\n\n"
                f"[LOCAL DATA — recommend ONLY from the list below]\n"
                f"{context_block}"
            )
            # Generate/update the map after every turn with real results
            _coords_for_map = coords if coords else current_coords
            if _coords_for_map:
                _map_path = generate_map(area, _coords_for_map, results)
        elif area:
            detected_cats = detect_categories(user_input)
            if detected_cats:
                print(
                    f"  [Area: {area.title()} | "
                    f"No data available for: {', '.join(detected_cats)}]"
                )
            else:
                print(f"  [Area: {area.title()} | No category detected — asking follow-up]")
            injected_content = user_input
        else:
            injected_content = user_input

        # ── call Ollama and append reply ─────────────────────────────
        conversation_history.append({"role": "user", "content": injected_content})
        reply = ask_ollama(conversation_history)
        conversation_history.append({"role": "assistant", "content": reply})
        _print_history(conversation_history)
        if _IN_NOTEBOOK and _map_path:
            display_map_in_notebook()


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("Run this module from a script or notebook that loads data first.")
    print("Example:")
    print("  from data_loader import load_city_data")
    print("  from agent import run_travel_agent")
    print('  data = load_city_data("USA", "NYC", "New York")')
    print('  run_travel_agent(data, radius=1.0, unit="km", location=("USA", "NYC", "New York"))')
