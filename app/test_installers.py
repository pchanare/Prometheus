"""
Quick diagnostic: run from the app/ directory to test find_local_installers.

Usage:
    cd app
    python test_installers.py "855 S 1st Street, Ann Arbor, Michigan"
"""
import os
import sys
import requests

MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")


def main():
    address = sys.argv[1] if len(sys.argv) > 1 else "855 S 1st Street, Ann Arbor, MI"
    print(f"\n=== Testing find_local_installers for: {address!r} ===\n")

    if not MAPS_API_KEY:
        print("ERROR: MAPS_API_KEY environment variable is not set!")
        sys.exit(1)
    print(f"MAPS_API_KEY: {MAPS_API_KEY[:8]}...{MAPS_API_KEY[-4:]}")

    # Step 1: Geocode
    print("\n[1] Geocoding address...")
    geo = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": MAPS_API_KEY},
    ).json()
    print(f"    status: {geo.get('status')}")
    if geo.get("status") != "OK":
        print(f"    error: {geo.get('error_message', '(none)')}")
        sys.exit(1)
    loc = geo["results"][0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]
    print(f"    lat={lat}, lng={lng}")

    # Step 2: Places API (New) — Text Search
    city_state = ", ".join(address.split(",")[1:]).strip() if "," in address else address
    query = f"solar panel installation companies near {city_state}"
    print(f"\n[2] Places API (New) Text Search...")
    print(f"    query: {query!r}")

    resp = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={
            "Content-Type":     "application/json",
            "X-Goog-Api-Key":   MAPS_API_KEY,
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
    data = resp.json()
    print(f"    HTTP status: {resp.status_code}")

    if not resp.ok:
        err = data.get("error", {})
        print(f"    ERROR: {err.get('message', resp.text)}")
        sys.exit(1)

    places = data.get("places", [])
    print(f"    results: {len(places)}")

    if not places:
        print("\nWARNING: No results returned.")
        sys.exit(0)

    print("\n[3] Top results:")
    for i, p in enumerate(places[:5]):
        name = p.get("displayName", {}).get("text", "(no name)")
        print(f"    {i+1}. {name!r}")
        print(f"       address : {p.get('formattedAddress')}")
        print(f"       rating  : {p.get('rating', 'N/A')}")

    print("\nSUCCESS: Real company names are being returned.\n")


if __name__ == "__main__":
    main()
