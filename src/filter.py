from geopy.distance import geodesic

# ─────────────────────────────────────────
# AREA COORDINATES
# ─────────────────────────────────────────
AREA_COORDINATES = {
    # Manhattan
    "midtown": (40.7549, -73.9840),
    "times square": (40.7580, -73.9855),
    "manhattan": (40.7831, -73.9712),
    "upper east side": (40.7736, -73.9566),
    "upper west side": (40.7870, -73.9754),
    "lower east side": (40.7157, -73.9863),
    "harlem": (40.8116, -73.9465),
    "soho": (40.7233, -73.9973),
    "tribeca": (40.7163, -74.0086),
    "financial district": (40.7074, -74.0113),
    "central park": (40.7851, -73.9683),

    # Outer boroughs
    "brooklyn": (40.6782, -73.9442),
    "queens": (40.7282, -73.7949),
    "bronx": (40.8448, -73.8648),
    "staten island": (40.5795, -74.1502),

    # Brooklyn neighborhoods
    "williamsburg": (40.7081, -73.9571),
    "dumbo": (40.7033, -73.9881),
    "park slope": (40.6710, -73.9814),
}

# ─────────────────────────────────────────
# CATEGORY KEYWORDS
# ─────────────────────────────────────────
CATEGORY_KEYWORDS = {
    "restaurants": ["eat", "food", "restaurant", "dinner", "lunch", "breakfast", "hungry", "dine"],
    "cafes":       ["coffee", "cafe", "tea", "latte", "espresso", "pasteries"],
    "bars":        ["bar", "drink", "drinks", "nightlife", "cocktail", "beer", "pub"],
    "hotels":      ["hotel", "stay", "sleep", "accommodation", "check in", "lodge"],
    "attractions": ["visit", "see", "attraction", "sightseeing", "things to do", "tourist"],
    "museums":     ["museum", "art", "history", "exhibit", "gallery"],
    "hospitals":   ["hospital", "injury", "emergency", "urgent care", "sick", "doctor", "medical", "clinic"],
    "pharmacies":  ["pharmacy", "medicine", "prescription", "drugstore", "cvs", "walgreens"],
    "atms":        ["atm", "cash", "money", "withdraw", "bank"],
    "subway_stations": ["subway", "metro", "train", "station", "transit", "underground"],
    "parks":       ["park", "outdoor", "walk", "nature", "garden", "green space"],

}

#Convert Miles to Km
def to_km(distance, unit="km"):
    if unit == "miles":
        return distance * 1.60934
    return distance

# Detect Area from Message
def detect_area(message):
    message = message.lower()
    for area, coords in AREA_COORDINATES.items():
        if area in message:
            return area, coords
    return None, None

# Detect categories from message
def detect_categories(message):
    message = message.lower()
    detected = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in message for keyword in keywords):
            detected.append(category)
    return detected

# Filters by Distance
def filter_nearby(df, coords, radius_km=1.0):
    df = df.copy()
    df["distance_km"] = df.apply(
        lambda row: geodesic(coords, (row["latitude"], row["longitude"])).km,
        axis=1
    )
    df["distance_miles"] = df["distance_km"] / 1.60934
    nearby = df[df["distance_km"] <= radius_km].sort_values("distance_km")

    if nearby.empty:
        print(f"No results found within {radius_km}km, expanding to {radius_km * 2}km...")
        nearby = df[df["distance_km"] <= radius_km * 2].sort_values("distance_km")

    return nearby

#Main Filter function
def filter_data(message, data, radius=1.0, unit="km"):
    area, coords = detect_area(message)
    categories = detect_categories(message)

    if not area:
        return None, None, []

    results = {}
    for category in categories:
        if category not in data:
            continue
        nearby = filter_nearby(data[category], coords, to_km(radius, unit))
        results[category] = nearby.head(20)

    return area, coords, results