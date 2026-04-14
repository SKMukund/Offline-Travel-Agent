# ─────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────
import re
import sys
import os
import ollama

try:
    from IPython.display import clear_output
    _IN_NOTEBOOK = True
except ImportError:
    _IN_NOTEBOOK = False

# Ensure src/ is on the path so this module works whether imported from the
# project root (e.g. a notebook) or run directly from within src/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from filter import filter_data, detect_categories, filter_nearby, to_km

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
# ASK OLLAMA
# ─────────────────────────────────────────
def ask_ollama(conversation_history: list[dict]) -> str:
    """Send the full conversation history to Ollama and return the reply."""
    response = ollama.chat(model=MODEL, messages=conversation_history)
    return response["message"]["content"]


# ─────────────────────────────────────────
# RUN TRAVEL AGENT
# ─────────────────────────────────────────
def _detect_radius(message: str) -> tuple[float | None, str | None]:
    """Extract a radius and unit from a user message, e.g. '2 km' or '1.5 miles'."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(km|mile|miles|mi)\b", message, re.IGNORECASE)
    if match:
        radius = float(match.group(1))
        unit = "miles" if match.group(2).lower() in ("mile", "miles", "mi") else "km"
        return radius, unit
    return None, None


def _print_history(conversation_history: list[dict]) -> None:
    """Clear the output and reprint the full chat in Agent/User format."""
    if _IN_NOTEBOOK:
        clear_output(wait=True)
    print("=" * 60)
    print("  Offline Travel Agent  (powered by llama3.2 via Ollama)")
    print("=" * 60 + "\n")
    for msg in conversation_history:
        if msg["role"] == "system":
            continue
        # Strip the injected LOCAL DATA block so the user sees their clean message
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


def run_travel_agent(data: dict, radius: float = 1.0, unit: str = "km") -> None:
    """
    Start the interactive travel agent loop.

    Parameters
    ----------
    data : dict
        Keys are category names (e.g. "restaurants"), values are Pandas DataFrames
        with columns: name, latitude, longitude.
    radius : float
        Default search radius.
    unit : str
        Default unit for the search radius — "km" or "miles".
    """
    conversation_history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    current_radius = radius
    current_unit = unit
    current_area: str | None = None
    current_coords: tuple | None = None

    greeting = (
        "Hi! I can help you find restaurants, cafes, bars, hotels, "
        "attractions, museums, parks, and more — all offline. "
        "Tell me where you are and what you're looking for. "
        "(Type 'quit' to exit, or include '2 km' / '1.5 miles' to change the search radius.)"
    )
    conversation_history.append({"role": "assistant", "content": greeting})
    _print_history(conversation_history)

    while True:
        # ── Get user input ──────────────────────────────────────────────
        try:
            user_input = input('You: ').strip()
        except (KeyboardInterrupt, EOFError):
            conversation_history.append({"role": "assistant", "content": "Goodbye! Safe travels!"})
            _print_history(conversation_history)
            break

        if user_input.lower() in ("quit", "exit", "q"):
            conversation_history.append({"role": "assistant", "content": "Goodbye! Safe travels!"})
            _print_history(conversation_history)
            break

        if not user_input:
            continue

        # ── Update radius if the user specified one ──────────────────────
        new_radius, new_unit = _detect_radius(user_input)
        if new_radius is not None:
            current_radius = new_radius
            current_unit = new_unit
            print(f"  [Search radius updated to {current_radius} {current_unit}]")

        # ── Filter local data ───────────────────────────────────────────
        area, coords, results = filter_data(
            user_input, data, current_radius, current_unit
        )

        # Persist area/coords across turns so the user doesn't have to
        # repeat their location in every message.
        if area:
            current_area = area
            current_coords = coords
        elif current_area and current_coords:
            # Area not in this message — fall back to stored location and
            # run filter_nearby directly for any newly mentioned categories.
            categories = detect_categories(user_input)
            if categories:
                area = current_area
                results = {}
                radius_km = to_km(current_radius, current_unit)
                for category in categories:
                    if category in data:
                        nearby = filter_nearby(data[category], current_coords, radius_km)
                        results[category] = nearby.head(20)

        # Build the content that goes into the conversation history.
        # When we have results, we prepend a LOCAL DATA block so Ollama
        # sees the places without them polluting the displayed chat.
        if area and results:
            total = sum(len(df) for df in results.values())
            categories_found = list(results.keys())
            print(
                f"  [Area: {area.title()} | "
                f"{total} result(s) in: {', '.join(categories_found)}]"
            )
            context_block = _results_to_context(area, results)
            injected_content = (
                f"{user_input}\n\n"
                f"[LOCAL DATA — recommend ONLY from the list below]\n"
                f"{context_block}"
            )
        elif area:
            detected_categories = detect_categories(user_input)
            if detected_categories:
                print(
                    f"  [Area: {area.title()} | "
                    f"No data available for: {', '.join(detected_categories)}]"
                )
            else:
                print(f"  [Area: {area.title()} | No category detected — asking follow-up]")
            injected_content = user_input
        else:
            injected_content = user_input

        # ── Send to Ollama and print reply ───────────────────────────────
        conversation_history.append({"role": "user", "content": injected_content})
        reply = ask_ollama(conversation_history)
        conversation_history.append({"role": "assistant", "content": reply})
        _print_history(conversation_history)


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────
if __name__ == "__main__":
    # Load data via data_loader before starting the agent.
    # Example (replace with real loader call):
    #
    #   from data_loader import load_city_data
    #   data = load_city_data("data/USA/New York/NYC")
    #   run_travel_agent(data)
    #
    print("Run this module from a script that loads data first.")
    print("Example:")
    print("  from data_loader import load_city_data")
    print("  from agent import run_travel_agent")
    print("  run_travel_agent(load_city_data('data/USA/New York/NYC'))")
