import geopandas as gpd
from pathlib import Path

# All possible categories and their useful columns
CATEGORIES = {
    "restaurants":     ["name", "cuisine", "addr:street", "latitude", "longitude"],
    "cafes":           ["name", "addr:street", "latitude", "longitude"],
    "bars":            ["name", "addr:street", "latitude", "longitude"],
    "hotels":          ["name", "addr:street", "latitude", "longitude"],
    "attractions":     ["name", "addr:street", "latitude", "longitude"],
    "museums":         ["name", "addr:street", "latitude", "longitude"],
    "subway_stations": ["name", "latitude", "longitude"],
    "parks":           ["name", "addr:street", "latitude", "longitude"],
    "pharmacies":      ["name", "addr:street", "latitude", "longitude"],
    "atms":            ["name", "addr:street", "latitude", "longitude"],
    "hospitals":       ["name", "addr:street", "latitude", "longitude"],
}

def load_city_data(country, city, state=None):
    if state:
        folder = Path("data") / country / state / city
    else:
        folder = Path("data") / country / city

    print(f"Loading from: {folder}")  
    print(f"Files found: {list(folder.glob('*.geojson'))}\n")
    loaded = {}

    for category, columns in CATEGORIES.items():
        path = folder / f"{category}.geojson"

        if not path.exists():
            print(f"Skipping {category} — file not found")
            continue

        print(f"Loading {category}...")
        gdf = gpd.read_file(path)
        gdf["latitude"] = gdf.geometry.to_crs(epsg=3857).centroid.to_crs(epsg=4326).y
        gdf["longitude"] = gdf.geometry.to_crs(epsg=3857).centroid.to_crs(epsg=4326).x

        # Only keep columns that actually exist in the file
        available_columns = [col for col in columns if col in gdf.columns]
        loaded[category] = gdf[available_columns].dropna(subset=["name"])

    print(f"\nLoaded {len(loaded)} categories for {city}!")
    return loaded