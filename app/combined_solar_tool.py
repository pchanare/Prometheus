"""
combined_solar_tool.py — Composite tool for combined rooftop + outdoor
(canopy / ground-mount) financial analysis.

Replaces the 2-step sequence:
  search_installation_cost → get_tax_benefits

In Gemini Live native audio, calling those two tools sequentially causes the
model to generate audio after each result.  This composite runs both internally
and returns one combined dict.
"""

import logging

log = logging.getLogger("prometheus.combined_solar_tool")

_PANEL_WATTAGE_W = 400


def calculate_combined_solar(
    matched_panels: int,
    matched_cost_usd: float,
    matched_annual_kwh: float,
    outdoor_panel_count: int,
    installation_type: str,
    state: str,
    yearly_sunshine_hours: float,
    electricity_rate_per_kwh: float = 0.16,
) -> dict:
    """
    Combined financial analysis for rooftop + canopy/ground-mount solar.

    Combines search_installation_cost and get_tax_benefits into ONE tool call
    so the model speaks exactly once after the result returns.

    Call this when the user asks for the combined system cost, savings, or
    payback after BOTH run_solar_analysis (rooftop) and analyze_space_for_solar
    (outdoor space) have already been called in this session.

    Args:
        matched_panels:         Rooftop panel count (from run_solar_analysis).
        matched_cost_usd:       Rooftop system cost before incentives.
        matched_annual_kwh:     Rooftop annual production.
        outdoor_panel_count:    Outdoor panel count (from analyze_space_for_solar).
        installation_type:      "canopy" or "ground_mount".
        state:                  Two-letter state code, e.g. "MI".
        yearly_sunshine_hours:  Annual peak sunshine hours (from run_solar_analysis).
        electricity_rate_per_kwh: Blended rate (from run_solar_analysis or 0.16).

    Returns:
        Dict with the full combined system breakdown:
          rooftop_panels, rooftop_cost_usd, rooftop_annual_kwh
          outdoor_panels, outdoor_cost_usd, outdoor_annual_kwh, installation_type
          total_panels, total_cost_usd, total_annual_kwh
          electricity_rate_per_kwh, estimated_annual_savings_usd
          federal_itc_savings_usd
          state_incentive_name, state_credit_usd
          total_incentives_usd
          revised_cost_usd, revised_payback_years
    """
    from search_installation_cost import search_installation_cost
    from tax_benefits import get_tax_benefits
    from search_tool import search_solar_incentives

    state             = (state or "").upper().strip()
    installation_type = (installation_type or "canopy").lower().strip()
    rate              = float(electricity_rate_per_kwh or 0.16)
    sunshine          = float(yearly_sunshine_hours or 0.0)

    # ── Step 1: Outdoor pricing ──────────────────────────────────────────────
    pricing       = search_installation_cost(outdoor_panel_count, installation_type, state)
    outdoor_cost  = pricing.get("total_cost_usd", 0)

    # ── Step 2: Outdoor production ───────────────────────────────────────────
    if sunshine > 0:
        cap_kw          = outdoor_panel_count * _PANEL_WATTAGE_W / 1000
        outdoor_kwh     = round(cap_kw * sunshine * 0.80, 1)
    else:
        outdoor_kwh     = 0.0

    # ── Step 3: Combined totals ──────────────────────────────────────────────
    total_panels  = int(matched_panels) + int(outdoor_panel_count)
    total_cost    = float(matched_cost_usd) + outdoor_cost
    total_kwh     = float(matched_annual_kwh) + outdoor_kwh

    # ── Step 4: Tax benefits on combined cost ────────────────────────────────
    estimated_annual_savings = round(total_kwh * rate)
    preliminary_payback      = round(total_cost / estimated_annual_savings, 1) if estimated_annual_savings else 0.0

    try:
        tax = get_tax_benefits(state, total_cost, preliminary_payback)
    except Exception as exc:
        log.warning("calculate_combined_solar: get_tax_benefits failed: %s", exc)
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

    # ── Step 5: Local incentive snippets ─────────────────────────────────────
    try:
        incentives = search_solar_incentives(state, total_cost)
        snippets   = incentives.get("incentive_snippets", [])
    except Exception as exc:
        log.warning("calculate_combined_solar: search_solar_incentives failed: %s", exc)
        snippets = []

    log.info(
        "calculate_combined_solar: %d+%d panels, combined cost=$%s, revised=$%s, payback=%.1f yrs",
        matched_panels, outdoor_panel_count, total_cost, revised_cost, revised_payback,
    )

    return {
        # Rooftop component
        "rooftop_panels":               int(matched_panels),
        "rooftop_cost_usd":             float(matched_cost_usd),
        "rooftop_annual_kwh":           float(matched_annual_kwh),
        # Outdoor component
        "outdoor_panels":               int(outdoor_panel_count),
        "outdoor_cost_usd":             outdoor_cost,
        "outdoor_annual_kwh":           outdoor_kwh,
        "installation_type":            installation_type,
        # Combined
        "total_panels":                 total_panels,
        "total_cost_usd":               round(total_cost),
        "total_annual_kwh":             round(total_kwh, 1),
        "electricity_rate_per_kwh":     rate,
        "estimated_annual_savings_usd": estimated_annual_savings,
        # Incentives (applied to combined cost)
        "federal_itc_savings_usd":      tax.get("federal_itc_savings_usd"),
        "state_incentive_name":         tax.get("state_incentive_name"),
        "state_credit_usd":             tax.get("state_credit_usd"),
        "total_incentives_usd":         tax.get("total_incentives_usd"),
        "revised_cost_usd":             revised_cost,
        "revised_payback_years":        revised_payback,
        "incentive_snippets":           snippets,
    }
