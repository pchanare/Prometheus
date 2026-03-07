import os

# Federal ITC is 30% as of 2024-2026
FEDERAL_ITC_RATE = 0.30

# State-level incentives
STATE_INCENTIVES = {
    "MI": {"name": "Michigan No state income tax credit", "rate": 0.0, "rebate": 0},
    "CA": {"name": "California Solar Initiative", "rate": 0.0, "rebate": 1000},
    "NY": {"name": "NY State Solar Credit", "rate": 0.25, "rebate": 5000},
    "TX": {"name": "Texas Property Tax Exemption", "rate": 0.0, "rebate": 0},
    "FL": {"name": "Florida Sales Tax Exemption", "rate": 0.0, "rebate": 500},
    "CO": {"name": "Colorado Solar Tax Credit", "rate": 0.30, "rebate": 0},
    "AZ": {"name": "Arizona Solar Tax Credit", "rate": 0.25, "rebate": 1000},
    "NJ": {"name": "New Jersey SREC Program", "rate": 0.0, "rebate": 2000},
    "MA": {"name": "Massachusetts SMART Program", "rate": 0.15, "rebate": 1000},
    "WA": {"name": "Washington Sales Tax Exemption", "rate": 0.0, "rebate": 800},
}

def get_tax_benefits(state: str, system_cost_usd: float, payback_years: float) -> dict:
    """
    Calculate federal and state tax benefits for solar installation.
    Returns revised costs and payback period after incentives.

    Args:
        state: Two-letter US state code e.g. 'MI', 'CA'
        system_cost_usd: Total upfront cost in USD
        payback_years: Original payback period in years

    Returns:
        Dict with all incentives and revised financials
    """
    state = state.upper().strip()

    # Federal ITC - 30% of system cost
    federal_itc = system_cost_usd * FEDERAL_ITC_RATE

    # State incentive
    state_info = STATE_INCENTIVES.get(
        state,
        {"name": "No specific state credit found", "rate": 0.0, "rebate": 0}
    )
    state_credit = (system_cost_usd * state_info["rate"]) + state_info["rebate"]

    # Total savings
    total_incentives = federal_itc + state_credit
    revised_cost = max(0, system_cost_usd - total_incentives)

    # Revised payback period
    if system_cost_usd > 0:
        revised_payback = payback_years * (revised_cost / system_cost_usd)
    else:
        revised_payback = 0

    return {
        "original_cost_usd": round(system_cost_usd, 2),
        "federal_itc_rate": f"{int(FEDERAL_ITC_RATE * 100)}%",
        "federal_itc_savings_usd": round(federal_itc, 2),
        "state": state,
        "state_incentive_name": state_info["name"],
        "state_credit_usd": round(state_credit, 2),
        "total_incentives_usd": round(total_incentives, 2),
        "revised_cost_usd": round(revised_cost, 2),
        "original_payback_years": round(payback_years, 1),
        "revised_payback_years": round(revised_payback, 1),
        "years_saved": round(payback_years - revised_payback, 1),
    }