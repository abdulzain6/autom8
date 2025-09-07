from typing import Any, List
import overpass

def get_nearby_pois(
    lat: float, lon: float, radius_m: int = 1000,
    categories: List[str] | None = None
) -> list[dict[str, Any]]:
    api = overpass.API(timeout=60)

    # Default set if not passed
    if categories is None:
        categories = [
            "doctors", "hospital", "pharmacy",
            "restaurant", "cafe", "fast_food",
            "bank", "atm", "school", "college",
            "university", "fuel", "bus_station",
            "supermarket", "cinema"
        ]

    # Build Overpass QL query dynamically
    query_parts = [f'node["amenity"="{c}"](around:{radius_m},{lat},{lon});' for c in categories]
    query = f"""
      (
        {"".join(query_parts)}
      );
    """

    response = api.get(query, responseformat="json")

    pois: list[dict[str, Any]] = []
    for element in response.get("elements", []):
        tags = element.get("tags", {})
        pois.append({
            "id": element["id"],
            "name": tags.get("name", "Unknown"),
            "category": tags.get("amenity", "Unknown"),
            "lat": element["lat"],
            "lon": element["lon"]
        })

    return pois


if __name__ == "__main__":
    results = get_nearby_pois(32.5173723, 74.5506706, 3000)
    for r in results:
        print(r)
