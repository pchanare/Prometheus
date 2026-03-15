"""
search_installation_cost.py — Live market cost estimator for canopy and
ground-mount solar installations.

Uses Google Custom Search to pull current pricing snippets, then passes
them to the Brain model to extract a realistic installed cost per panel
for the given installation type and state.

Why not a hardcoded rate?
  - Canopy systems carry structural costs (pergola frame, posts, roof load
    engineering) not present in rooftop installs -> typically $1,000-$1,500/panel.
  - Ground mounts need racking, trenching, and a DC cable run -> typically
    $900-$1,100/panel.
  - Both vary by state (labour rates, permit costs, utility interconnection fees).
  - A live search anchors the estimate to today's market rather than a figure
    that was accurate 2 years ago.
"""

import json
import logging
import os

import requests

log = logging.getLogger("prometheus.installation_cost")

_SEARCH_API_KEY   = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "")

# Sensible fallbacks if search or Brain extraction fails entirely
_FALLBACK_COST_PER_PANEL = {
    "canopy":       1200,   # mid-range of $1,000-$1,500 installed
    "ground_mount":  950,   # mid-range of $900-$1,100 installed
}


def _google_search(query: str, num: int = 3) -> list:
    """Return up to *num* snippet strings from Google Custom Search."""
    if not _SEARCH_API_KEY or not _SEARCH_ENGINE_ID:
        log.warning("search_installation_cost: missing API key or engine ID — skipping search")
        return []
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": _SEARCH_API_KEY,
                "cx":  _SEARCH_ENGINE_ID,
                "q":   query,
                "num": num,
            },
            timeout=8,
        )
        if not resp.ok:
            log.warning("search_installation_cost: search HTTP %d for %r", resp.status_code, query)
            return []
        return [
            (item.get("snippet") or "").strip()[:250]
            for item in resp.json().get("items", [])
            if (item.get("snippet") or "").strip()
        ]
    except Exception as exc:
        log.warning("search_installation_cost: search error: %s", exc)
        return []


def _extract_cost_from_snippets(snippets, installation_type, panel_count, state):
    """
    Pass search snippets to the Brain model and ask it to extract a
    realistic all-in installed cost estimate.
    """
    if not snippets:
        fallback = _FALLBACK_COST_PER_PANEL.get(installation_type, 1000)
        return {
            "cost_per_panel_usd": fallback,
            "total_cost_usd":     fallback * panel_count,
            "confidence":         "low",
            "source_note":        "No search results available — using industry average fallback.",
        }

    snippet_block = "\n".join(f"- {s}" for s in snippets)
    install_label = "solar canopy / pergola" if installation_type == "canopy" else "ground-mount solar"

    prompt = (
        f"You are a solar installation cost analyst. A homeowner in {state} wants to install "
        f"a {install_label} system with {panel_count} panels.\n\n"
        f"Here are recent web search snippets about {install_label} installation costs:\n"
        f"{snippet_block}\n\n"
        "Based on these snippets and your knowledge of current (2025) US solar market pricing, "
        f"estimate the all-in installed cost per panel for a {install_label} system in {state}. "
        "'All-in' means: equipment (panels, inverter, mounting hardware, structural frame for canopy), "
        "labour, permits, and utility interconnection fees included.\n\n"
        "Return ONLY a valid JSON object with exactly these fields:\n"
        '{"cost_per_panel_usd": <integer>, "confidence": "<low|medium|high>", '
        '"source_note": "<one sentence explaining the basis for this estimate>"}\n'
        "Return ONLY the JSON object, no markdown fences, no extra text."
    )

    try:
        from brain import call_brain
        raw = call_brain(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        cost_per_panel = int(data.get("cost_per_panel_usd", _FALLBACK_COST_PER_PANEL.get(installation_type, 1000)))
        return {
            "cost_per_panel_usd": cost_per_panel,
            "total_cost_usd":     cost_per_panel * panel_count,
            "confidence":         str(data.get("confidence", "medium")),
            "source_note":        str(data.get("source_note", "")),
        }
    except Exception as exc:
        log.warning("search_installation_cost: Brain extraction failed: %s", exc)
        fallback = _FALLBACK_COST_PER_PANEL.get(installation_type, 1000)
        return {
            "cost_per_panel_usd": fallback,
            "total_cost_usd":     fallback * panel_count,
            "confidence":         "low",
            "source_note":        f"Brain extraction failed ({exc}) — using industry average fallback.",
        }


def search_installation_cost(panel_count: int, installation_type: str, state: str) -> dict:
    """
    Estimate the current all-in installed cost for a canopy or ground-mount
    solar system by searching for live market pricing and extracting a
    realistic per-panel cost via the Brain model.

    Args:
        panel_count:       Number of solar panels in the system.
        installation_type: Either "canopy" or "ground_mount".
        state:             US state name or two-letter code (e.g. "Michigan" or "MI").

    Returns:
        Dict with cost_per_panel_usd, total_cost_usd, confidence, source_note,
        installation_type, panel_count, state, search_snippets.
    """
    installation_type = (installation_type or "canopy").lower().strip()
    if installation_type not in ("canopy", "ground_mount"):
        installation_type = "canopy"

    install_label = "solar canopy pergola" if installation_type == "canopy" else "ground mount solar"

    queries = [
        f"{install_label} installation cost per panel {state} 2025",
        f"{install_label} total installed cost {panel_count} panels USA 2025",
        f"how much does {install_label} cost installed 2025",
    ]

    all_snippets = []
    for q in queries:
        if len(all_snippets) >= 6:
            break
        all_snippets.extend(_google_search(q, num=2))

    log.info("search_installation_cost: %d snippets for %s/%s panels in %s",
             len(all_snippets), installation_type, panel_count, state)

    cost_data = _extract_cost_from_snippets(all_snippets, installation_type, panel_count, state)

    return {
        "installation_type":  installation_type,
        "panel_count":        panel_count,
        "state":              state,
        "cost_per_panel_usd": cost_data["cost_per_panel_usd"],
        "total_cost_usd":     cost_data["total_cost_usd"],
        "confidence":         cost_data["confidence"],
        "source_note":        cost_data["source_note"],
        "search_snippets":    all_snippets,
    }
