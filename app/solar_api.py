import os
import googlemaps
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

_MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")
_SOLAR_BASE = "https://solar.googleapis.com/v1/buildingInsights:findClosest"

def get_solar_data(address: str) -> dict:
    """Geocode address and return full solar potential data including cost and payback."""

    # 1. Geocode
    gmaps = googlemaps.Client(key=_MAPS_API_KEY)
    results = gmaps.geocode(address)
    if not results:
        raise ValueError(f"Could not geocode address: {address!r}")

    location = results[0]["geometry"]["location"]
    lat, lng = location["lat"], location["lng"]

    # 2. Solar API
    response = requests.get(
        _SOLAR_BASE,
        params={
            "location.latitude": lat,
            "location.longitude": lng,
            "requiredQuality": "LOW",
            "key": _MAPS_API_KEY,
        },
    )
    if not response.ok:
        raise ValueError(f"Solar API error {response.status_code}: {response.text}")

    data = response.json()
    solar = data.get("solarPotential", {})

    # 3. Extract financial data - try multiple locations in response
    financial_analyses = solar.get("financialAnalyses", [])
    
    upfront_cost = "N/A"
    payback_years = "N/A"
    
    # Loop through analyses to find one with financial data
    for analysis in financial_analyses:
        cash = analysis.get("cashPurchaseSavings", {})
        if cash:
            cost = cash.get("outOfPocketCost", {})
            upfront_cost = f"${cost.get('units', 'N/A')}"
            payback_years = cash.get("paybackYears", "N/A")
            break

    return {
        "address": address,
        "max_panels": solar.get("maxArrayPanelsCount"),
        "roof_area_m2": round(solar.get("maxArrayAreaMeters2", 0), 1),
        "yearly_sunshine_hours": round(solar.get("maxSunshineHoursPerYear", 0), 1),
        "panel_capacity_watts": solar.get("panelCapacityWatts"),
        "upfront_cost_usd": upfront_cost,
        "payback_years": payback_years,
        "panel_lifetime_years": solar.get("panelLifetimeYears"),
    }