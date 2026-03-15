"""
solar_analysis_tool.py — Composite tool that runs the full address-based
solar analysis in a single model tool call.

WHY THIS EXISTS
---------------
The address analysis previously called 4 separate tools sequentially:
  get_solar_data → get_tax_benefits → search_solar_incentives → web_search

In Gemini Live native audio, every time ADK delivers a tool result back to
the model, the model is free to generate audio immediately.  It doesn't wait
for all tools to finish before speaking.  The result was 5+ interleaved audio
segments within a single turn — the model spoke after each tool returned,
then kept speaking after the final result, looping back into financial
breakdowns and repeating the solar analysis.

Because all of this happened inside ONE turn (turn_complete hadn't fired yet),
user_spoke stayed True the entire time and the loop guard couldn't help.

This composite tool makes ONE call to the model, runs all API work internally,
and returns everything in a single result dict.  The model then speaks exactly
once and stop cleanly.
"""

import logging

log = logging.getLogger("prometheus.solar_analysis_tool")


def run_solar_analysis(address: str, monthly_bill_usd: float, state: str) -> dict:
    """
    Complete solar analysis for a property address: solar potential, tax
    benefits, and local incentive snippets — all in one tool call.

    Call this when the user provides a home address for solar analysis.
    Do NOT call get_solar_data, get_tax_benefits, or search_solar_incentives
    separately for address-based analysis — use this tool instead.

    Args:
        address:          Full property address (street, city, state).
        monthly_bill_usd: User's average monthly electricity bill in USD.
        state:            Two-letter state code, e.g. 'MI', 'CA'.

    Returns:
        Dict with everything needed to present the full solar analysis:
          Solar potential ──────────────────────────────────────────────
          address, monthly_bill_usd
          yearly_sunshine_hours   – annual peak sunshine hours
          matched_panels          – panel count to offset this bill
          matched_annual_kwh      – system annual energy production
          matched_cost_usd        – all-in system cost (pre-incentives)
          roof_area_m2            – total usable roof area
          max_panels              – maximum the roof can fit physically
          Electricity rate ─────────────────────────────────────────────
          electricity_rate_per_kwh – estimated $/kWh from bill + production
          estimated_annual_savings_usd – matched_annual_kwh × rate
          Tax & incentives ─────────────────────────────────────────────
          federal_itc_savings_usd
          state_incentive_name
          state_credit_usd
          total_incentives_usd
          revised_cost_usd        – cost after all known incentives
          revised_payback_years
          Local incentives ─────────────────────────────────────────────
          incentive_snippets      – raw web snippets for any extra rebates
    """
    from solar_api import get_solar_data
    from tax_benefits import get_tax_benefits
    from search_tool import search_solar_incentives

    # ── Step 1: Solar potential ──────────────────────────────────────────────
    solar = get_solar_data(address, monthly_bill_usd)

    matched_panels  = solar.get("matched_panels", 0)
    matched_cost    = solar.get("matched_cost_usd", 0)
    matched_payback = solar.get("matched_payback_years", 0.0)
    matched_kwh     = solar.get("matched_annual_kwh", 0.0)

    # ── Step 2: Electricity rate (estimated from bill and solar production) ──
    # Rate ≈ annual_bill / annual_production.  This is the user's effective
    # blended rate — more accurate than a web search for their specific plan.
    if matched_kwh > 0:
        electricity_rate = round((monthly_bill_usd * 12) / matched_kwh, 4)
    else:
        electricity_rate = 0.16  # national average fallback

    estimated_annual_savings = round(matched_kwh * electricity_rate)

    # ── Step 3: Tax benefits ─────────────────────────────────────────────────
    state_code = state.upper().strip()
    try:
        tax = get_tax_benefits(state_code, matched_cost, matched_payback)
    except Exception as exc:
        log.warning("run_solar_analysis: get_tax_benefits failed: %s", exc)
        federal_itc = round(matched_cost * 0.30)
        tax = {
            "federal_itc_savings_usd": federal_itc,
            "state_incentive_name":    f"State incentives for {state_code} (lookup failed)",
            "state_credit_usd":        0.0,
            "total_incentives_usd":    float(federal_itc),
            "revised_cost_usd":        round(matched_cost - federal_itc),
            "revised_payback_years":   round(matched_payback * 0.70, 1),
        }

    # ── Step 4: Local incentive snippets ────────────────────────────────────
    try:
        incentives = search_solar_incentives(state_code, matched_cost)
        snippets   = incentives.get("incentive_snippets", [])
    except Exception as exc:
        log.warning("run_solar_analysis: search_solar_incentives failed: %s", exc)
        snippets = []

    log.info(
        "run_solar_analysis: %d panels, $%s cost, $%s revised for %r",
        matched_panels, matched_cost, tax.get("revised_cost_usd"), address,
    )

    # Persist key facts so future sessions skip asking for them.
    try:
        from session_memory import update as _smem_update
        _smem_update(
            address=address,
            state=state_code,
            monthly_bill_usd=monthly_bill_usd,
            yearly_sunshine_hours=solar.get("yearly_sunshine_hours"),
            roof_area_m2=solar.get("roof_area_m2"),
        )
    except Exception as _se:
        log.warning("run_solar_analysis: session_memory update failed: %s", _se)

    return {
        # Solar potential
        "address":                      address,
        "monthly_bill_usd":             monthly_bill_usd,
        "yearly_sunshine_hours":        solar.get("yearly_sunshine_hours"),
        "matched_panels":               matched_panels,
        "matched_annual_kwh":           matched_kwh,
        "matched_cost_usd":             matched_cost,
        "roof_area_m2":                 solar.get("roof_area_m2"),
        "max_panels":                   solar.get("max_panels"),
        # Electricity rate
        "electricity_rate_per_kwh":     electricity_rate,
        "estimated_annual_savings_usd": estimated_annual_savings,
        # Tax and incentives
        "federal_itc_savings_usd":      tax.get("federal_itc_savings_usd"),
        "state_incentive_name":         tax.get("state_incentive_name"),
        "state_credit_usd":             tax.get("state_credit_usd"),
        "total_incentives_usd":         tax.get("total_incentives_usd"),
        "revised_cost_usd":             tax.get("revised_cost_usd"),
        "revised_payback_years":        tax.get("revised_payback_years"),
        # Local incentive snippets for any additional rebates
        "incentive_snippets":           snippets,
    }
