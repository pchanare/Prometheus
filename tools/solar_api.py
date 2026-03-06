"""Solar API tool: geocode an address and fetch building solar insights."""

import os

import googlemaps
import google.auth
from google.maps import solar_v1
from google.type import latlng_pb2

PROJECT_ID = "prometheus-489421"
_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")


def get_solar_data(address: str) -> dict:
    """Geocode *address* and return solar potential data for the building.

    Authentication:
        - Geocoding (googlemaps): reads the ``GOOGLE_MAPS_API_KEY`` env var.
        - Solar API: uses Application Default Credentials (``gcloud auth
          application-default login``).

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
        ValueError: If the address cannot be geocoded.
        google.api_core.exceptions.GoogleAPICallError: On Solar API errors.
    """
    # --- 1. Geocode ---
    gmaps = googlemaps.Client(key=_MAPS_API_KEY)
    results = gmaps.geocode(address)
    if not results:
        raise ValueError(f"Could not geocode address: {address!r}")

    location = results[0]["geometry"]["location"]
    lat, lng = location["lat"], location["lng"]

    # --- 2. Solar API ---
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    solar_client = solar_v1.SolarClient(credentials=credentials)

    request = solar_v1.FindClosestBuildingInsightsRequest(
        location=latlng_pb2.LatLng(latitude=lat, longitude=lng),
        required_quality=solar_v1.ImageryQuality.HIGH,
    )
    response = solar_client.find_closest_building_insights(request=request)

    solar = response.solar_potential
    return {
        "max_array_panels_count": solar.max_array_panels_count,
        "yearly_max_sunshine_hours": solar.max_sunshine_hours_per_year,
    }
