"""
solar_api.py — Google Solar API wrapper with bill-matched scenario fitting.

The Solar API returns 7-8 financial scenarios, each targeting a different
monthly electricity bill tier (e.g. $50, $100, $150, $200, $300, $400, $500).
Each scenario has its own matched panel count, system cost, and payback period.

PROBLEM with naive approach:
  Taking max_panels from the top-level object and cost from the first scenario
  produces wildly inconsistent numbers (e.g. 323 panels quoted at $7,400).
  They come from completely different parts of the API response.

THIS APPROACH:
  1. Extract ALL scenario data points: bill -> panels, bill -> cost, bill -> payback,
     bill -> annual_kwh.
  2. Fit polynomial curves through all data points (degree 2 if >=3 points, else 1).
  3. Evaluate the curves at the user's ACTUAL monthly bill — interpolating within
     the API's range, or extrapolating beyond it using the fitted curve.
  4. All four output values (panels, cost, payback, annual_kwh) now come from the
     SAME mathematical model of the house — fully coherent estimates.
"""

import logging
import os

import googlemaps
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

log = logging.getLogger("prometheus.solar_api")

_MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")
_SOLAR_BASE   = "https://solar.googleapis.com/v1/buildingInsights:findClosest"


# ---------------------------------------------------------------------------
# Curve fitting helpers
# ---------------------------------------------------------------------------

def _fit_and_evaluate(x_vals: list, y_vals: list, x_target: float,
                      floor: float = 0.0) -> float:
    """
    Fit a polynomial through (x_vals, y_vals) and evaluate at x_target.
    Uses numpy polyfit (degree = min(2, n-1)) with linear interpolation fallback.
    """
    n = len(x_vals)
    if n == 0:
        return floor
    if n == 1:
        return max(floor, float(y_vals[0]))

    try:
        import numpy as np
        x = np.array(x_vals, dtype=float)
        y = np.array(y_vals, dtype=float)
        degree = min(2, n - 1)
        coeffs = np.polyfit(x, y, degree)
        result = float(np.polyval(coeffs, x_target))
        return max(floor, result)
    except ImportError:
        pass
    except Exception as exc:
        log.warning("_fit_and_evaluate: polyfit failed (%s) — using linear fallback", exc)

    # Linear fallback: interpolate or extrapolate between nearest two points
    pairs = sorted(zip(x_vals, y_vals))
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]

    if x_target <= xs[0]:
        x0, y0, x1, y1 = xs[0], ys[0], xs[1], ys[1]
    elif x_target >= xs[-1]:
        x0, y0, x1, y1 = xs[-2], ys[-2], xs[-1], ys[-1]
    else:
        x0, y0, x1, y1 = xs[-2], ys[-2], xs[-1], ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= x_target <= xs[i + 1]:
                x0, y0, x1, y1 = xs[i], ys[i], xs[i + 1], ys[i + 1]
                break

    if x1 == x0:
        return max(floor, float(y0))

    slope = (y1 - y0) / (x1 - x0)
    return max(floor, y0 + slope * (x_target - x0))


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def get_solar_data(address: str, monthly_bill_usd: float = 150.0) -> dict:
    """
    Geocode the address, call the Google Solar API, extract all financial
    scenario data points, fit interpolation/extrapolation curves, and return
    coherent estimates matched to the user's actual monthly electricity bill.

    Args:
        address:          Full property address string.
        monthly_bill_usd: User's average monthly electricity bill in USD.
                          Defaults to $150 if not provided.

    Returns:
        Dict with solar potential data matched to the user's bill:
          - address, monthly_bill_usd
          - matched_panels:         panel count to offset this bill
          - matched_cost_usd:       all-in system cost for this panel count
          - matched_payback_years:  payback period for this scenario
          - matched_annual_kwh:     annual energy production
          - roof_area_m2:           total usable roof area
          - yearly_sunshine_hours:  annual peak sunshine hours
          - panel_capacity_watts:   watt rating per panel
          - panel_lifetime_years:   expected system lifespan
          - max_panels:             maximum panels the roof can physically fit
          - scenario_count:         how many bill scenarios the API returned
          - bill_range_usd:         [min_bill, max_bill] covered by API scenarios
          - extrapolated:           True if user's bill is outside API range
    """
    try:
        from status_channel import push_status as _push_status
        _push_status("☀️ Fetching solar potential data for your address…")
    except Exception:
        pass

    monthly_bill_usd = float(monthly_bill_usd or 150.0)

    # 1. Geocode
    gmaps   = googlemaps.Client(key=_MAPS_API_KEY)
    results = gmaps.geocode(address)
    if not results:
        raise ValueError(f"Could not geocode address: {address!r}")

    location = results[0]["geometry"]["location"]
    lat, lng = location["lat"], location["lng"]

    # 2. Solar API call
    response = requests.get(
        _SOLAR_BASE,
        params={
            "location.latitude":  lat,
            "location.longitude": lng,
            "requiredQuality":    "LOW",
            "key":                _MAPS_API_KEY,
        },
    )
    if not response.ok:
        raise ValueError(f"Solar API error {response.status_code}: {response.text}")

    data  = response.json()
    solar = data.get("solarPotential", {})

    # 3. Build data point lists from all financial scenarios
    financial_analyses  = solar.get("financialAnalyses", [])
    solar_panel_configs = solar.get("solarPanelConfigs", [])

    bills_usd    = []
    panels_list  = []
    costs_list   = []
    payback_list = []
    kwh_list     = []

    for analysis in financial_analyses:
        bill_obj = analysis.get("monthlyBill", {})
        bill_val = bill_obj.get("units")
        if bill_val is None:
            continue
        try:
            bill = float(bill_val)
        except (TypeError, ValueError):
            continue

        config_idx = analysis.get("panelConfigIndex")
        panels = kwh = None
        if config_idx is not None and config_idx < len(solar_panel_configs):
            cfg    = solar_panel_configs[config_idx]
            panels = cfg.get("panelsCount")
            kwh    = cfg.get("yearlyEnergyDcKwh")

        cash     = analysis.get("cashPurchaseSavings", {})
        if not cash:
            continue
        cost_obj = cash.get("outOfPocketCost", {})
        cost_val = cost_obj.get("units")
        payback  = cash.get("paybackYears")

        if None not in (panels, kwh, cost_val, payback):
            try:
                bills_usd.append(bill)
                panels_list.append(float(panels))
                costs_list.append(float(cost_val))
                payback_list.append(float(payback))
                kwh_list.append(float(kwh))
            except (TypeError, ValueError):
                continue

    log.info("get_solar_data: %d scenario data points for %r", len(bills_usd), address)

    # 4. Fit curves and evaluate at user's bill
    bill_min     = min(bills_usd) if bills_usd else 50.0
    bill_max     = max(bills_usd) if bills_usd else 500.0
    extrapolated = (monthly_bill_usd < bill_min or monthly_bill_usd > bill_max)

    if bills_usd:
        matched_panels  = round(_fit_and_evaluate(bills_usd, panels_list,  monthly_bill_usd, floor=1.0))
        matched_cost    = round(_fit_and_evaluate(bills_usd, costs_list,   monthly_bill_usd, floor=0.0))
        matched_payback = round(_fit_and_evaluate(bills_usd, payback_list, monthly_bill_usd, floor=0.0), 1)
        matched_kwh     = round(_fit_and_evaluate(bills_usd, kwh_list,     monthly_bill_usd, floor=0.0), 1)
    else:
        log.warning("get_solar_data: no usable scenarios — using top-level fallback")
        matched_panels  = solar.get("maxArrayPanelsCount") or 0
        matched_cost    = 0
        matched_payback = 0
        matched_kwh     = 0

    # 5. Build result
    result = {
        "address":               address,
        "monthly_bill_usd":      monthly_bill_usd,
        "matched_panels":        matched_panels,
        "matched_cost_usd":      matched_cost,
        "matched_payback_years": matched_payback,
        "matched_annual_kwh":    matched_kwh,
        "roof_area_m2":          round(solar.get("maxArrayAreaMeters2", 0), 1),
        "yearly_sunshine_hours": round(solar.get("maxSunshineHoursPerYear", 0), 1),
        "panel_capacity_watts":  solar.get("panelCapacityWatts"),
        "panel_lifetime_years":  solar.get("panelLifetimeYears"),
        "max_panels":            solar.get("maxArrayPanelsCount"),
        "scenario_count":        len(bills_usd),
        "bill_range_usd":        [bill_min, bill_max],
        "extrapolated":          extrapolated,
    }

    # 6. Persist to session memory
    try:
        from session_memory import update as _mem
        _mem(
            address=address,
            monthly_bill_usd=monthly_bill_usd,
            roof_area_m2=result["roof_area_m2"],
            yearly_sunshine_hours=result["yearly_sunshine_hours"],
        )
    except Exception as exc:
        log.warning("get_solar_data: session memory update failed: %s", exc)

    return result
