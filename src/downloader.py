import osmnx as ox
from pathlib import Path

# ─────────────────────────────────────────
# TOGGLE WHAT YOU WANT TO DOWNLOAD
# ─────────────────────────────────────────
def ask_downloads():
    print("What would you like to download? (y/n)\n")
    
    selections = {}
    for tag in TAGS:
        while True:
            answer = input(f"{tag}? (y/n): ").strip().lower()
            if answer == "y":
                selections[tag] = True
                break
            elif answer == "n" or answer == "":
                selections[tag] = False
                break
            else:
                print(f"Invalid input '{answer}' — please enter y or n")
    
    return selections

# ─────────────────────────────────────────
# OSM TAGS FOR EACH CATEGORY
# ─────────────────────────────────────────
TAGS = {
    "restaurants":     {"amenity": "restaurant"},
    "cafes":           {"amenity": "cafe"},
    "bars":            {"amenity": "bar"},
    "hotels":          {"tourism": "hotel"},
    "attractions":     {"tourism": "attraction"},
    "museums":         {"tourism": "museum"},
    "subway_stations": {"railway": "station"},
    "parks":           {"leisure": "park"},
    "pharmacies":      {"amenity": "pharmacy"},
    "atms":            {"amenity": "atm"},
    "hospitals":       {"amenity": "hospital"},
}

# ─────────────────────────────────────────
# DOWNLOADER
# ─────────────────────────────────────────

# With a state input
def download_city_data(country, city, state=None):
    base_folder="data"

    #Throw Exceptions
    if not country or not isinstance(country, str):
        raise ValueError("Country must be a non-empty string")
    
    if not city or not isinstance(city, str):
        raise ValueError("City must be a non-empty string")
    
    #Check if location has a state and build folder path
    if state:
        folder = Path(base_folder) / country / state / city
        osm_query = f"{city}, {state}, {country}"
    else:
        folder = Path(base_folder) / country / city
        osm_query = f"{city}, {country}"
        
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Saving to: {folder}\n")

    selected = ask_downloads()

    for category, enabled in selected.items():
        if not enabled:
            print(f"Skipping {category}...")
            continue

        print(f"Downloading {category}...")
        gdf = ox.features_from_place(osm_query, tags=TAGS[category])

        output_path = folder / f"{category}.geojson"
        gdf.to_file(output_path, driver="GeoJSON")
        print(f"Saved {len(gdf)} {category} → {output_path}")

    print(f"\nAll downloads complete for {city}!")

