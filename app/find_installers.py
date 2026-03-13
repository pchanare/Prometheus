import logging
import os
import requests

log = logging.getLogger("prometheus.find_installers")

_MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")

# Places API (New) endpoint — replaces the legacy textsearch/json
_PLACES_NEW_URL = "https://places.googleapis.com/v1/places:searchText"


def find_local_installers(address: str) -> dict:
    """
    Find top 3 local solar installation companies near the given address.
    Uses the Places API (New) — https://developers.google.com/maps/documentation/places/web-service/text-search

    Returns:
        Dict with 3 local solar companies and hardcoded demo email ids
    """
    try:
        from status_channel import push_status as _push_status
        _push_status("📋 Finding solar installers near you…")
    except Exception:
        pass

    try:
        # ── Step 1: Geocode the address ──────────────────────────────────────
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

        # ── Step 2: Places API (New) — Text Search ───────────────────────────
        city_state = ", ".join(address.split(",")[1:]).strip() if "," in address else address

        payload = {
            "textQuery": f"solar panel installation companies near {city_state}",
            "maxResultCount": 5,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": 50000.0,
                }
            },
        }

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": _MAPS_API_KEY,
            # Request only the fields we need (billing-efficient)
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.rating",
        }

        places_response = requests.post(_PLACES_NEW_URL, json=payload, headers=headers)
        log.info("Places API (New) HTTP status: %s", places_response.status_code)

        if not places_response.ok:
            raise ValueError(
                f"Places API (New) error {places_response.status_code}: {places_response.text}"
            )

        places_data = places_response.json()
        places = places_data.get("places", [])
        log.info("Places API (New) returned %d results", len(places))

        # ── Step 3: Build company list ────────────────────────────────────────
        demo_emails = [
            "csaipraneethreddy+installer1@gmail.com",
            "csaipraneethreddy+installer2@gmail.com",
            "csaipraneethreddy+installer3@gmail.com",
        ]

        companies = []
        for i, place in enumerate(places[:3]):
            name = place.get("displayName", {}).get("text", f"Local Solar Installer {i + 1}")
            companies.append({
                "name":    name,
                "address": place.get("formattedAddress", f"Near {address}"),
                "rating":  place.get("rating", "N/A"),
                "email":   demo_emails[i],
            })
            log.info("Installer %d: %s", i + 1, name)

        # Fallback if fewer than 3 real results came back
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
