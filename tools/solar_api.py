"""Solar API tool: geocode an address and fetch building solar insights."""

import os

import googlemaps
import requests

_MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")
_SOLAR_BASE = "https://solar.googleapis.com/v1/buildingInsights:findClosest"


def get_solar_data(address: str) -> dict:
    """Geocode *address* and return solar potential data for the building.

    Authentication:
        Both geocoding and the Solar API use the ``MAPS_API_KEY`` env var.

    Args:
        address: A human-readable street address, e.g.
            "1600 Amphitheatre Pkwy, Mountain View, CA".

    Returns:
        A dict with:
            ``max_array_panels_count`` (int): Maximum number of solar panels
                that can fit on the roof.
            ``yearly_max_sunshine_hours`` (float): Maximum sunshine hours per
                year across all roof segments.

    Raises:
        ValueError: If the address cannot be geocoded or the Solar API errors.
    """
    # --- 1. Geocode ---
    gmaps = googlemaps.Client(key=_MAPS_API_KEY)
    results = gmaps.geocode(address)
    if not results:
        raise ValueError(f"Could not geocode address: {address!r}")

    location = results[0]["geometry"]["location"]
    lat, lng = location["lat"], location["lng"]

    # --- 2. Solar REST API ---
    response = requests.get(
        _SOLAR_BASE,
        params={
            "location.latitude": lat,
            "location.longitude": lng,
            "requiredQuality": "HIGH",
            "key": _MAPS_API_KEY,
        },
    )
    if not response.ok:
        raise ValueError(f"Solar API error {response.status_code}: {response.text}")

    solar = response.json().get("solarPotential", {})
    return {
        "max_array_panels_count": solar.get("maxArrayPanelsCount"),
        "yearly_max_sunshine_hours": solar.get("maxSunshineHoursPerYear"),
    }
