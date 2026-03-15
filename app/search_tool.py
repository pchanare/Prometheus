import logging
import os
import requests

log = logging.getLogger("prometheus.search_tool")

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
    # NOTE: push_status is intentionally NOT called here.
    # search_solar_incentives is called internally by run_solar_analysis,
    # which already owns the status pill via before_tool_callback.
    # Calling push_status from a thread (run_coroutine_threadsafe) queues the
    # message on the next event-loop tick — AFTER after_tool_callback has already
    # sent the clear — so the stale text overwrites the blank and the pill stays.
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


def web_search(query: str) -> dict:
    """
    Search the web using Google Custom Search and return relevant snippets.

    Use this tool to look up any real-time information needed for solar
    analysis, such as:
      - Current residential electricity rates for a city or state
        e.g. query="average residential electricity rate Ann Arbor Michigan 2025 per kWh"
      - Local utility company net metering policies
      - Current solar panel prices or installation costs
      - Any other factual data needed to complete a calculation

    Args:
        query: The search query string. Be specific — include the city/state
               and year for best results.

    Returns:
        Dict with up to 5 search result snippets and their source URLs.
    """
    log.info("web_search: %r", query)

    snippets = []
    try:
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": _SEARCH_API_KEY,
                "cx": _SEARCH_ENGINE_ID,
                "q": query,
                "num": 5,
            },
            timeout=10,
        )
        if response.ok:
            for item in response.json().get("items", []):
                title   = (item.get("title") or "").strip()
                snippet = (item.get("snippet") or "").strip()
                link    = (item.get("link") or "").strip()
                if snippet:
                    snippets.append({
                        "title":   title[:120],
                        "snippet": snippet[:250],
                        "url":     link,
                    })
        else:
            log.warning("web_search HTTP %s: %s", response.status_code, response.text[:200])
    except Exception as exc:
        log.error("web_search failed: %s", exc)

    return {
        "query":   query,
        "results": snippets,
        "note": (
            "Extract the specific data point you need (e.g. $/kWh rate) from "
            "these snippets. If the snippets don't contain a clear answer, use "
            "the national average of $0.16/kWh as a fallback."
        ),
    }