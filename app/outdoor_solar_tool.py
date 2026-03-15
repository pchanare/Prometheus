"""
outdoor_solar_tool.py — Composite tool for individual canopy / ground-mount
financial analysis.

Replaces the 3-step sequence:
  search_installation_cost → get_tax_benefits → search_solar_incentives

In Gemini Live native audio, calling those three tools sequentially causes the
model to generate audio after each result — creating overlapping speech within
a single turn.  This composite runs all three internally and returns one combined
dict, so the model makes one call, stays silent, and speaks once.
"""

import logging

log = logging.getLogger("prometheus.outdoor_solar_tool")

_PANEL_WATTAGE_W = 400   # standard panel wattage assumption


def calculate_outdoor_solar(
    panel_count: int,
    installation_type: str,
    state: str,
    yearly_sunshine_hours: float = 0.0,
    annual_energy_kwh: float = 0.0,
    electricity_rate_per_kwh: float = 0.16,
) -> dict:
    """
    Complete financial analysis for a canopy or ground-mount solar system.

    Combines live market pricing, tax benefits, and local incentive search into
    ONE tool call so the model speaks exactly once after the result returns.

    Call this EXACTLY ONCE per image upload or user request — never call it
    multiple times with different panel counts or scenarios for the same image.
    Use the panel_count from analyze_space_for_solar unchanged.

    Do NOT call search_installation_cost, get_tax_benefits, or
    search_solar_incentives separately for this flow.

    Args:
        panel_count:            Number of panels (from analyze_space_for_solar).
        installation_type:      "canopy" or "ground_mount".
        state:                  Two-letter state code, e.g. "MI".
        yearly_sunshine_hours:  Annual peak sunshine hours (from run_solar_analysis
                                if the address was already analysed; else 0).
        annual_energy_kwh:      Annual kWh from image analysis tool — used when
                                yearly_sunshine_hours is not available.
        electricity_rate_per_kwh: User's blended rate. Pass electricity_rate_per_kwh
                                from run_solar_analysis if known; defaults to the
                                US national average of $0.16/kWh.

    Returns:
        Dict with everything needed to present the outdoor solar estimate:
          installation_type, panel_count, state
          cost_per_panel_usd, total_cost_usd, cost_confidence
          annual_production_kwh
          electricity_rate_per_kwh, estimated_annual_savings_usd
          preliminary_payback_years
          federal_itc_savings_usd
          state_incentive_name, state_credit_usd
          total_incentives_usd
          revised_cost_usd, revised_payback_years
          incentive_snippets
    """
    from search_installation_cost import search_installation_cost
    from tax_benefits import get_tax_benefits
    from search_tool import search_solar_incentives

    panel_count        = int(panel_count or 0)
    installation_type  = (installation_type or "canopy").lower().strip()
    state              = (state or "").upper().strip()
    sunshine           = float(yearly_sunshine_hours or 0.0)
    kwh_from_image     = float(annual_energy_kwh or 0.0)
    rate               = float(electricity_rate_per_kwh or 0.16)

    # ── Step 1: Live market pricing ──────────────────────────────────────────
    pricing = search_installation_cost(panel_count, installation_type, state)
    total_cost      = pricing.get("total_cost_usd", 0)
    cost_per_panel  = pricing.get("cost_per_panel_usd", 0)
    confidence      = pricing.get("confidence", "medium")

    # ── Step 2: Annual production ────────────────────────────────────────────
    if sunshine > 0:
        capacity_kw          = panel_count * _PANEL_WATTAGE_W / 1000
        annual_production    = round(capacity_kw * sunshine * 0.80, 1)
    elif kwh_from_image > 0:
        annual_production    = kwh_from_image
    else:
        # Minimal fallback: assume 1 500-hr equivalent sun days average
        capacity_kw          = panel_count * _PANEL_WATTAGE_W / 1000
        annual_production    = round(capacity_kw * 1500 * 0.80, 1)

    # ── Step 3: Preliminary financial figures ────────────────────────────────
    estimated_annual_savings   = round(annual_production * rate)
    preliminary_payback        = round(total_cost / estimated_annual_savings, 1) if estimated_annual_savings else 0.0

    # ── Step 4: Tax benefits ─────────────────────────────────────────────────
    try:
        tax = get_tax_benefits(state, total_cost, preliminary_payback)
    except Exception as exc:
        log.warning("calculate_outdoor_solar: get_tax_benefits failed: %s", exc)
        federal_itc = round(total_cost * 0.30)
        tax = {
            "federal_itc_savings_usd": federal_itc,
            "state_incentive_name":    f"State incentives for {state} (lookup failed)",
            "state_credit_usd":        0.0,
            "total_incentives_usd":    float(federal_itc),
            "revised_cost_usd":        round(total_cost - federal_itc),
            "revised_payback_years":   round(preliminary_payback * 0.70, 1),
        }

    revised_cost    = tax.get("revised_cost_usd", total_cost)
    revised_payback = round(revised_cost / estimated_annual_savings, 1) if estimated_annual_savings else 0.0

    # ── Step 5: Local incentive snippets ────────────────────────────────────
    try:
        incentives = search_solar_incentives(state, total_cost)
        snippets   = incentives.get("incentive_snippets", [])
    except Exception as exc:
        log.warning("calculate_outdoor_solar: search_solar_incentives failed: %s", exc)
        snippets = []

    log.info(
        "calculate_outdoor_solar: %s %d panels, cost=$%s, revised=$%s, payback=%.1f yrs",
        installation_type, panel_count, total_cost, revised_cost, revised_payback,
    )

    return {
        "installation_type":            installation_type,
        "panel_count":                  panel_count,
        "state":                        state,
        "cost_per_panel_usd":           cost_per_panel,
        "total_cost_usd":               total_cost,
        "cost_confidence":              confidence,
        "annual_production_kwh":        annual_production,
        "electricity_rate_per_kwh":     rate,
        "estimated_annual_savings_usd": estimated_annual_savings,
        "preliminary_payback_years":    preliminary_payback,
        "federal_itc_savings_usd":      tax.get("federal_itc_savings_usd"),
        "state_incentive_name":         tax.get("state_incentive_name"),
        "state_credit_usd":             tax.get("state_credit_usd"),
        "total_incentives_usd":         tax.get("total_incentives_usd"),
        "revised_cost_usd":             revised_cost,
        "revised_payback_years":        revised_payback,
        "incentive_snippets":           snippets,
    }
