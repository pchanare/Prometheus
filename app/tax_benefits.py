import json
import logging

log = logging.getLogger("prometheus.tax_benefits")

# Federal ITC is 30% as of 2024-2026 (stable federal law)
FEDERAL_ITC_RATE = 0.30

# Known states — fast path, no AI call needed.
# For any other state, call_brain() is used to look up current incentives.
STATE_INCENTIVES = {
    "MI": {"name": "Michigan No state income tax credit",  "rate": 0.0,  "rebate": 0},
    "CA": {"name": "California Solar Initiative",          "rate": 0.0,  "rebate": 1000},
    "NY": {"name": "NY State Solar Credit",                "rate": 0.25, "rebate": 5000},
    "TX": {"name": "Texas Property Tax Exemption",         "rate": 0.0,  "rebate": 0},
    "FL": {"name": "Florida Sales Tax Exemption",          "rate": 0.0,  "rebate": 500},
    "CO": {"name": "Colorado Solar Tax Credit",            "rate": 0.30, "rebate": 0},
    "AZ": {"name": "Arizona Solar Tax Credit",             "rate": 0.25, "rebate": 1000},
    "NJ": {"name": "New Jersey SREC Program",              "rate": 0.0,  "rebate": 2000},
    "MA": {"name": "Massachusetts SMART Program",          "rate": 0.15, "rebate": 1000},
    "WA": {"name": "Washington Sales Tax Exemption",       "rate": 0.0,  "rebate": 800},
}


def _brain_state_incentives(state: str) -> dict:
    """
    Ask the Brain model for solar tax incentives for a state not in the
    hardcoded table.  Returns {name, rate, rebate} or safe defaults on failure.
    """
    try:
        from brain import call_brain
        prompt = (
            f"What are the current (2025) state-level solar tax incentives for {state}? "
            "Return ONLY a valid JSON object with exactly these fields:\n"
            '{"name": "<incentive programme name>", "rate": <credit rate 0.0-1.0>, "rebate": <flat rebate USD>}\n'
            "Where:\n"
            "  rate   = state income tax credit as a decimal (e.g. 0.25 for 25%)\n"
            "  rebate = any flat dollar rebate on top of the rate credit\n"
            "If there is no state credit, use rate: 0.0 and rebate: 0.\n"
            "Return ONLY the JSON object, no markdown, no explanation."
        )
        raw = call_brain(prompt)
        # Strip accidental markdown fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return {
            "name":   str(data.get("name",   f"{state} Solar Incentive (AI)")),
            "rate":   float(data.get("rate",   0.0)),
            "rebate": float(data.get("rebate", 0.0)),
        }
    except Exception as exc:
        log.warning("_brain_state_incentives failed for %s: %s — returning 0", state, exc)
        return {"name": f"No state incentive data found for {state}", "rate": 0.0, "rebate": 0.0}


def get_tax_benefits(state: str, system_cost_usd: float, payback_years: float) -> dict:
    """
    Calculate federal and state tax benefits for solar installation.
    Returns revised costs and payback period after incentives.

    Known states are resolved instantly from the hardcoded table.
    All other states are looked up via the Brain model (gemini-3.1-flash-lite-preview).

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

    # Federal ITC — 30% of system cost (stable through 2032)
    federal_itc = system_cost_usd * FEDERAL_ITC_RATE

    # State incentive — fast path for known states, brain fallback for all others
    if state in STATE_INCENTIVES:
        state_info = STATE_INCENTIVES[state]
        log.info("get_tax_benefits: %s resolved from table", state)
    else:
        log.info("get_tax_benefits: %s not in table — querying Brain", state)
        state_info = _brain_state_incentives(state)

    state_credit = (system_cost_usd * state_info["rate"]) + state_info["rebate"]

    # Total savings and revised cost
    total_incentives = federal_itc + state_credit
    revised_cost = max(0.0, system_cost_usd - total_incentives)

    # Revised payback period scales proportionally with cost reduction
    revised_payback = (payback_years * (revised_cost / system_cost_usd)
                       if system_cost_usd > 0 else 0.0)

    result = {
        "original_cost_usd":      round(system_cost_usd, 2),
        "federal_itc_rate":       f"{int(FEDERAL_ITC_RATE * 100)}%",
        "federal_itc_savings_usd": round(federal_itc, 2),
        "state":                  state,
        "state_incentive_name":   state_info["name"],
        "state_credit_usd":       round(state_credit, 2),
        "total_incentives_usd":   round(total_incentives, 2),
        "revised_cost_usd":       round(revised_cost, 2),
        "original_payback_years": round(payback_years, 1),
        "revised_payback_years":  round(revised_payback, 1),
        "years_saved":            round(payback_years - revised_payback, 1),
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
