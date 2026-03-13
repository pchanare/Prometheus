import logging
import os
import requests

log = logging.getLogger("prometheus.find_installers")

_MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")


def find_local_installers(address: str) -> dict:
    """
    Find top 3 local solar installation companies near the given address.

    Args:
        address: The property address to search near

    Returns:
        Dict with 3 local solar companies and hardcoded demo email ids
    """
    try:
        # Step 1: Geocode the address (Geocoding API is not legacy)
        geocode_data = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": _MAPS_API_KEY},
        ).json()
        log.info("Geocode status: %s", geocode_data.get("status"))

        if not geocode_data.get("results"):
            raise ValueError(f"Geocode failed: {geocode_data.get('status')}")

        location = geocode_data["results"][0]["geometry"]["location"]
        lat, lng = location["lat"], location["lng"]

        # Step 2: Places API (New) — Text Search
        # POST https://places.googleapis.com/v1/places:searchText
        city_state = ", ".join(address.split(",")[1:]).strip() if "," in address else address
        query = f"solar panel installation companies near {city_state}"
        log.info("Places API (New) query: %r  location: %s,%s", query, lat, lng)

        places_resp = requests.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "Content-Type":     "application/json",
                "X-Goog-Api-Key":   _MAPS_API_KEY,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.rating",
            },
            json={
                "textQuery":    query,
                "maxResultCount": 5,
                "locationBias": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lng},
                        "radius": 50000.0,
                    }
                },
            },
        )
        places_data = places_resp.json()
        log.info("Places API (New) response status: %s | places: %d",
                 places_resp.status_code, len(places_data.get("places", [])))

        if not places_resp.ok:
            err = places_data.get("error", {})
            raise ValueError(f"Places API error {places_resp.status_code}: {err.get('message', places_resp.text)}")

        results = places_data.get("places", [])[:3]

        # Step 3: Build company list — real names from Google, demo emails for testing
        demo_emails = [
            "csaipraneethreddy+installer1@gmail.com",
            "csaipraneethreddy+installer2@gmail.com",
            "csaipraneethreddy+installer3@gmail.com",
        ]

        companies = []
        for i, place in enumerate(results):
            name = place.get("displayName", {}).get("text", f"Solar Installer {i + 1}")
            companies.append({
                "name":    name,
                "address": place.get("formattedAddress", f"Near {address}"),
                "rating":  place.get("rating", "N/A"),
                "email":   demo_emails[i],
            })
            log.info("Installer %d: %s", i + 1, name)

        # Fallback if fewer than 3 real results
        while len(companies) < 3:
            idx = len(companies)
            companies.append({
                "name":    f"Local Solar Installer {idx + 1}",
                "address": f"Near {address}",
                "rating":  "N/A",
                "email":   demo_emails[idx],
            })

        return {
            "status":    "success",
            "location":  address,
            "companies": companies,
        }

    except Exception as e:
        log.error("find_local_installers failed: %s", e)
        return {
            "status":    "failed",
            "error":     str(e),
            "companies": [],
        }
