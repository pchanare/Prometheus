import datetime
import json
import logging
import os

import requests

log = logging.getLogger("prometheus.tax_benefits")

_SEARCH_API_KEY   = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "")

# Federal ITC is 30% through 2032 (Inflation Reduction Act).
# From 2033 onward the rate is scheduled to step down, so we search for the
# current rate rather than relying on a hardcoded constant.
_FEDERAL_ITC_KNOWN_RATE   = 0.30
_FEDERAL_ITC_KNOWN_UNTIL  = 2032   # last year the 30% rate is guaranteed


def _google_search(query: str, num: int = 3) -> list:
    """Return up to *num* snippet strings from Google Custom Search."""
    if not _SEARCH_API_KEY or not _SEARCH_ENGINE_ID:
        log.warning("tax_benefits: missing API key or engine ID — skipping search")
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
            log.warning("tax_benefits: search HTTP %d for %r", resp.status_code, query)
            return []
        return [
            (item.get("snippet") or "").strip()[:250]
            for item in resp.json().get("items", [])
            if (item.get("snippet") or "").strip()
        ]
    except Exception as exc:
        log.warning("tax_benefits: search error: %s", exc)
        return []


def _get_federal_itc_rate() -> tuple[float, str]:
    """
    Return (rate, source_note) for the federal Investment Tax Credit.

    Before 2033 the rate is the known 0.30 and no search is needed.
    From 2033 onward we search Google for the current rate and ask Brain
    to extract it; if that fails we fall back to 0.26 (the legislated
    step-down value) so estimates remain conservative rather than wrong.
    """
    current_year = datetime.date.today().year
    if current_year <= _FEDERAL_ITC_KNOWN_UNTIL:
        return _FEDERAL_ITC_KNOWN_RATE, f"Federal ITC {int(_FEDERAL_ITC_KNOWN_RATE * 100)}% (locked in through {_FEDERAL_ITC_KNOWN_UNTIL})"

    # Post-2032 — search for the current rate
    log.info("_get_federal_itc_rate: year %d > %d — searching for current federal ITC", current_year, _FEDERAL_ITC_KNOWN_UNTIL)
    snippets = []
    for q in [
        f"federal solar investment tax credit ITC rate {current_year}",
        f"residential clean energy credit percentage {current_year}",
    ]:
        snippets.extend(_google_search(q, num=2))
        if len(snippets) >= 4:
            break

    snippet_block = "\n".join(f"- {s}" for s in snippets) if snippets else "(no snippets)"
    prompt = (
        f"It is {current_year}. A homeowner wants to know the current US federal "
        "solar Investment Tax Credit (ITC) rate, also called the Residential "
        "Clean Energy Credit.\n\n"
        f"Recent web snippets:\n{snippet_block}\n\n"
        "Return ONLY a valid JSON object:\n"
        '{"rate": <decimal 0.0-1.0>, "note": "<one sentence describing the current rate and any phase-down schedule>"}\n'
        "Return ONLY the JSON, no markdown, no extra text."
    )
    fallback_rate = 0.26  # legislated step-down after 2032
    try:
        from brain import call_brain
        raw = call_brain(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        rate = float(data.get("rate", fallback_rate))
        note = str(data.get("note", f"Federal ITC {int(rate * 100)}% ({current_year})"))
        return rate, note
    except Exception as exc:
        log.warning("_get_federal_itc_rate: Brain extraction failed: %s — using fallback %.0f%%", exc, fallback_rate * 100)
        return fallback_rate, f"Federal ITC {int(fallback_rate * 100)}% ({current_year} fallback — verify current rate)"

def _search_state_incentives(state: str) -> list:
    """
    Run 3 targeted Google Custom Search queries for state solar tax incentives.
    Returns a deduplicated list of up to 6 snippet strings.
    """
    queries = [
        f"{state} state solar tax credit incentive 2025",
        f"{state} solar rebate utility program 2025",
        f"{state} solar income tax credit rate percentage 2025",
    ]
    snippets = []
    for q in queries:
        if len(snippets) >= 6:
            break
        snippets.extend(_google_search(q, num=2))
    return snippets


def _brain_fallback_incentive(state: str) -> dict:
    """
    Ask Brain for state incentives using training knowledge when live search
    is unavailable (quota exhausted, 403, network error).
    Returns {name, rate, rebate} or safe zeros on failure.
    """
    prompt = (
        f"What are the current (2025) state-level solar tax incentives for {state}? "
        "Use your training knowledge — no web search available.\n"
        "Return ONLY a valid JSON object with exactly these fields:\n"
        '{"name": "<official programme name>", "rate": <credit rate 0.0-1.0>, "rebate": <flat rebate USD>}\n'
        "Where:\n"
        "  rate   = state income tax credit as a decimal (e.g. 0.25 for 25%)\n"
        "  rebate = any flat dollar rebate on top of the rate credit\n"
        "If there is no state income tax credit, use rate: 0.0 and rebate: 0.\n"
        "Return ONLY the JSON object, no markdown, no explanation."
    )
    try:
        from brain import call_brain
        raw = call_brain(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = __import__("json").loads(raw.strip())
        result = {
            "name":   str(data.get("name",   f"{state} Solar Incentive (estimated)")),
            "rate":   float(data.get("rate",   0.0)),
            "rebate": float(data.get("rebate", 0.0)),
        }
        log.info("tax_benefits: Brain fallback for %s → rate=%.2f rebate=%.0f",
                 state, result["rate"], result["rebate"])
        return result
    except Exception as exc:
        log.warning("tax_benefits: Brain fallback failed for %s: %s — returning 0", state, exc)
        return {"name": f"No state incentive data for {state}", "rate": 0.0, "rebate": 0.0}


def _extract_incentive_from_snippets(state: str, snippets: list) -> dict:
    """
    Pass live search snippets to the Brain model and ask it to extract a
    structured state incentive object.  Returns {name, rate, rebate}.
    Falls back to zero incentives on any failure.
    """
    if not snippets:
        # Search unavailable (quota/403) — fall back to Brain training knowledge.
        # Not as current as live data but far better than returning 0 for states
        # that have real credits (NY 25%, AZ 25%, etc.)
        log.warning("tax_benefits: no snippets for %s — falling back to Brain training data", state)
        return _brain_fallback_incentive(state)

    snippet_block = "\n".join(f"- {s}" for s in snippets)

    prompt = (
        f"You are a solar policy analyst. A homeowner in {state} wants to know "
        f"about current (2025) state-level solar tax incentives.\n\n"
        f"Here are recent web search snippets about {state} solar incentives:\n"
        f"{snippet_block}\n\n"
        "Based on these snippets and your knowledge of current US solar policy, "
        f"extract the state-level solar income tax credit for {state}.\n\n"
        "Return ONLY a valid JSON object with exactly these fields:\n"
        '{"name": "<official programme name>", "rate": <credit rate 0.0-1.0>, "rebate": <flat rebate USD>}\n'
        "Where:\n"
        "  name   = official name of the state programme (e.g. 'NY State Solar Credit')\n"
        "  rate   = state income tax credit as a decimal (e.g. 0.25 for 25%)\n"
        "  rebate = any flat dollar rebate on top of the percentage credit\n"
        "If there is no state income tax credit, use rate: 0.0.\n"
        "If there is no flat rebate, use rebate: 0.\n"
        "Return ONLY the JSON object, no markdown fences, no explanation."
    )

    try:
        from brain import call_brain
        raw = call_brain(prompt).strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return {
            "name":   str(data.get("name",   f"{state} Solar Incentive")),
            "rate":   float(data.get("rate",   0.0)),
            "rebate": float(data.get("rebate", 0.0)),
        }
    except Exception as exc:
        log.warning(
            "tax_benefits: Brain extraction failed for %s: %s — returning 0", state, exc
        )
        return {
            "name":   f"No state incentive data found for {state}",
            "rate":   0.0,
            "rebate": 0.0,
        }


def get_tax_benefits(state: str, system_cost_usd: float, payback_years: float) -> dict:
    """
    Calculate federal and state tax benefits for solar installation.
    Returns revised costs and payback period after incentives.

    State incentives are looked up via live Google Custom Search and then
    extracted by the Brain model — no hardcoded table, always current data.

    Args:
        state: Two-letter US state code e.g. 'MI', 'CA', 'OR'
        system_cost_usd: Total upfront cost in USD
        payback_years: Original payback period in years

    Returns:
        Dict with all incentives and revised financials
    """
    state = state.upper().strip()

    try:
        from status_channel import push_status as _push_status
        _push_status("💰 Calculating federal and state tax incentives…")
    except Exception:
        pass

    # Federal ITC — rate is stable through 2032; searched live from 2033 onward
    federal_itc_rate, federal_itc_note = _get_federal_itc_rate()
    federal_itc = system_cost_usd * federal_itc_rate

    # State incentive — live search + Brain extraction
    log.info("get_tax_benefits: searching live incentives for %s", state)
    snippets   = _search_state_incentives(state)
    state_info = _extract_incentive_from_snippets(state, snippets)

    state_credit = (system_cost_usd * state_info["rate"]) + state_info["rebate"]

    # Total savings and revised cost
    total_incentives = federal_itc + state_credit
    revised_cost     = max(0.0, system_cost_usd - total_incentives)

    # Revised payback period scales proportionally with cost reduction
    revised_payback = (
        payback_years * (revised_cost / system_cost_usd)
        if system_cost_usd > 0 else 0.0
    )

    result = {
        "original_cost_usd":       round(system_cost_usd, 2),
        "federal_itc_rate":        f"{int(federal_itc_rate * 100)}%",
        "federal_itc_note":        federal_itc_note,
        "federal_itc_savings_usd": round(federal_itc, 2),
        "state":                   state,
        "state_incentive_name":    state_info["name"],
        "state_credit_usd":        round(state_credit, 2),
        "total_incentives_usd":    round(total_incentives, 2),
        "revised_cost_usd":        round(revised_cost, 2),
        "original_payback_years":  round(payback_years, 1),
        "revised_payback_years":   round(revised_payback, 1),
        "years_saved":             round(payback_years - revised_payback, 1),
    }

    try:
        from session_memory import update as _mem
        _mem(
            state=state,
            revised_cost_usd=result["revised_cost_usd"],
            revised_payback_years=result["revised_payback_years"],
        )
    except Exception:
        pass

    return result
