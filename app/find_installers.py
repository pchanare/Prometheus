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
        from status_channel import push_status as _push_status
        _push_status("📋 Finding solar installers near you…")
    except Exception:
        pass

    try:
        # Step 1: Geocode the address
        geocode_response = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": _MAPS_API_KEY},
        )
        geocode_data = geocode_response.json()
        log.info("Geocode status: %s", geocode_data.get("status"))

        if not geocode_data.get("results"):
            raise ValueError(f"Geocode failed: {geocode_data.get('status')}")

        location = geocode_data["results"][0]["geometry"]["location"]
        lat, lng = location["lat"], location["lng"]

        # Step 2: Text Search with location bias using geocoded coordinates.
        # Using `location` + `radius` biases results toward the address area.
        city_state = ", ".join(address.split(",")[1:]).strip() if "," in address else address
        places_response = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={
                "query":    f"solar panel installation companies near {city_state}",
                "location": f"{lat},{lng}",
                "radius":   50000,
                "key":      _MAPS_API_KEY,
            },
        )
        places_data = places_response.json()
        api_status = places_data.get("status")
        log.info("Places Text Search status: %s | results: %d",
                 api_status, len(places_data.get("results", [])))

        if api_status not in ("OK", "ZERO_RESULTS"):
            raise ValueError(f"Places API error: {api_status} — {places_data.get('error_message', '')}")

        results = places_data.get("results", [])[:3]

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
                "name":    place.get("name"),
                "address": place.get("formatted_address") or place.get("vicinity"),
                "rating":  place.get("rating", "N/A"),
                "email":   demo_emails[i],
            })
            log.info("Installer %d: %s", i + 1, place.get("name"))

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
