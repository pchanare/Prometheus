import os
import requests

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
        # Step 1: Geocode the address
        geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
        geocode_response = requests.get(
            geocode_url,
            params={
                "address": address,
                "key": _MAPS_API_KEY
            }
        )
        
        if not geocode_response.ok:
            raise ValueError("Could not geocode address")
            
        geocode_data = geocode_response.json()
        if not geocode_data["results"]:
            raise ValueError("No results found for address")
            
        location = geocode_data["results"][0]["geometry"]["location"]
        lat, lng = location["lat"], location["lng"]
        
        # Step 2: Search for solar companies nearby
        places_url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        places_response = requests.get(
            places_url,
            params={
                "location": f"{lat},{lng}",
                "radius": 50000,  # 50km radius
                "keyword": "solar panel installation company",
                "type": "establishment",
                "key": _MAPS_API_KEY
            }
        )
        
        if not places_response.ok:
            raise ValueError("Could not search for installers")
            
        places_data = places_response.json()
        results = places_data.get("results", [])[:3]
        
        # Step 3: Build company list with hardcoded demo emails
        demo_emails = [
            "csaipraneethreddy+installer1@gmail.com",  # ← replace with your email
            "csaipraneethreddy+installer2@gmail.com",  # ← replace with your email
            "csaipraneethreddy+installer3@gmail.com",  # ← replace with your email
        ]
        
        companies = []
        for i, place in enumerate(results):
            companies.append({
                "name": place.get("name"),
                "address": place.get("vicinity"),
                "rating": place.get("rating", "N/A"),
                "email": demo_emails[i],
            })
        
        # Fallback if less than 3 results
        while len(companies) < 3:
            companies.append({
                "name": f"Local Solar Installer {len(companies) + 1}",
                "address": "Near " + address,
                "rating": "N/A",
                "email": demo_emails[len(companies)],
            })
            
        return {
            "status": "success",
            "location": address,
            "companies": companies
        }
        
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "companies": []
        }