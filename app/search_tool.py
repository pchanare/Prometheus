import os
import requests

_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "")

def search_solar_incentives(state: str, system_cost_usd: float) -> dict:
    """
    Search the web for real-time solar tax incentives and rebates for a given state.
    
    Args:
        state: Full state name or two-letter code e.g. 'Michigan' or 'MI'
        system_cost_usd: Total system cost in USD to calculate potential savings
    
    Returns:
        Dict with search results about incentives and estimated savings
    """
    queries = [
        f"{state} solar tax incentives rebates 2025",
        f"{state} utility solar rebate programs 2025",
        f"federal solar ITC tax credit 2025",
    ]
    
    all_results = []
    
    for query in queries:
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": _SEARCH_API_KEY,
                "cx": _SEARCH_ENGINE_ID,
                "q": query,
                "num": 3,
            },
        )
        
        if response.ok:
            data = response.json()
            items = data.get("items", [])
            for item in items:
                all_results.append({
                    "title": item.get("title"),
                    "snippet": item.get("snippet"),
                    "link": item.get("link"),
                })
    
    return {
        "state": state,
        "system_cost_usd": system_cost_usd,
        "search_results": all_results,
        "note": "Use these results to identify and calculate all available incentives and revised payback period"
    }