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
    try:
        from status_channel import push_status as _push_status
        _push_status("🔍 Searching for local solar incentives and rebates…")
    except Exception:
        pass

    queries = [
        f"{state} solar tax incentives rebates 2025",
        f"{state} utility solar rebate programs 2025",
        f"federal solar ITC tax credit 2025",
    ]
    
    snippets = []

    for query in queries:
        if len(snippets) >= 5:
            break
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
            for item in response.json().get("items", []):
                snippet = (item.get("snippet") or "").strip()
                if snippet and len(snippets) < 5:
                    # Truncate each snippet to 180 chars — enough to identify the incentive
                    snippets.append(snippet[:180])

    return {
        "state":            state,
        "system_cost_usd":  system_cost_usd,
        "incentive_snippets": snippets,
        "note": (
            "Use these snippets to identify available incentives, name them clearly, "
            "and factor them into the revised payback period calculation."
        ),
    }