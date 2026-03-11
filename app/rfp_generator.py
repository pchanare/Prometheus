import logging
from datetime import datetime

log = logging.getLogger("prometheus.rfp_generator")

# ── Server-side RFP store ────────────────────────────────────────────────────
# Email bodies are stored here keyed by company_name so they never need to be
# returned to the voice model (which would consume thousands of context tokens).
# send_rfp_email.py reads from this store at send time.
_rfp_store: dict[str, dict] = {}   # company_name → {subject, email_content}


def generate_rfp(
    address: str,
    yearly_sunshine_hours: float,
    max_panels: int,
    roof_area_m2: float,
    roof_age_years: int,
    monthly_bill_usd: float,
    homeowner_name: str,
    ground_mount_analysis: str = "Not provided",
    company_name: str = "Solar Installation Company",
) -> dict:
    """
    Generate a professional, personalised solar installation inquiry email using
    the Brain model (gemini-3.1-flash-lite-preview → gemini-2.5-pro).

    The Brain is given all property data and the required email structure so it
    can write a genuinely tailored message rather than a generic template.
    Falls back to the original static template if the Brain call fails.

    Args:
        address: Property address
        yearly_sunshine_hours: Annual sunshine hours
        max_panels: Maximum number of panels
        roof_area_m2: Roof area in square meters
        roof_age_years: Age of roof in years
        monthly_bill_usd: Average monthly electricity bill
        homeowner_name: Name of the homeowner
        ground_mount_analysis: Analysis of ground mount potential (optional)
        company_name: Name of the installation company

    Returns:
        Dict with email content, subject, address, date, company_name
    """
    today = datetime.now().strftime("%B %d, %Y")
    roof_area_sqft       = round(roof_area_m2 * 10.764, 1)
    roof_install_year    = datetime.now().year - roof_age_years
    monthly_consumption  = round(monthly_bill_usd / 0.12)

    try:
        from session_memory import update as _mem
        _mem(
            homeowner_name=homeowner_name,
            monthly_bill_usd=monthly_bill_usd,
            roof_age_years=roof_age_years,
        )
    except Exception:
        pass

    address_parts = address.split(",")
    city_state = ", ".join(address_parts[1:]).strip() if len(address_parts) > 1 else address

    ground_mount_section = (
        f"\nI am also interested in exploring ground-mounted solar options for my outdoor space. "
        f"Based on a preliminary assessment of my property:\n\n{ground_mount_analysis}\n\n"
        "Please include ground-mounted system options in your proposal if applicable.\n"
        if ground_mount_analysis != "Not provided" else ""
    )

    # ── Ask Brain to write a personalised version ───────────────────────────
    email_content = _brain_rfp(
        address=address,
        city_state=city_state,
        homeowner_name=homeowner_name,
        company_name=company_name,
        today=today,
        roof_area_m2=roof_area_m2,
        roof_area_sqft=roof_area_sqft,
        roof_install_year=roof_install_year,
        roof_age_years=roof_age_years,
        yearly_sunshine_hours=yearly_sunshine_hours,
        max_panels=max_panels,
        monthly_bill_usd=monthly_bill_usd,
        monthly_consumption=monthly_consumption,
        ground_mount_section=ground_mount_section,
    )

    subject = f"Solar Installation Inquiry - {city_state}"

    # Store email server-side — never return the body to the voice model.
    # send_rfp_email will retrieve it from here at send time.
    _rfp_store[company_name] = {
        "subject":       subject,
        "email_content": email_content,
    }
    log.info("generate_rfp: stored %d-char email for %r (total stored: %d)",
             len(email_content), company_name, len(_rfp_store))

    return {
        "status":       "success",
        "company_name": company_name,
        "subject":      subject,
        "address":      address,
        "rfp_ready":    True,
        "summary":      (
            f"RFP email prepared for {company_name} at {address}. "
            f"Subject: {subject!r}. Ready to send."
        ),
    }


def _brain_rfp(**kw) -> str:
    """
    Use Brain to write a personalised RFP email.
    Returns a static fallback template if the Brain call fails.
    """
    try:
        from brain import call_brain

        prompt = f"""Write a professional solar installation inquiry email on behalf of a homeowner.

PROPERTY DATA — use all of these precisely in the relevant sections:
  Homeowner      : {kw['homeowner_name']}
  Address        : {kw['address']}
  Today's date   : {kw['today']}
  Recipient      : {kw['company_name']}
  Roof area      : {kw['roof_area_sqft']} sq ft ({kw['roof_area_m2']} m²)
  Roof installed : {kw['roof_install_year']} ({kw['roof_age_years']} years old)
  Annual sunshine: {kw['yearly_sunshine_hours']} hours/year
  Panel potential: up to {kw['max_panels']} panels
  Monthly bill   : ${kw['monthly_bill_usd']:,.0f}/month
  Monthly usage  : ~{kw['monthly_consumption']} kWh/month{(chr(10) + '  Ground mount  : ' + kw['ground_mount_section'].strip()) if kw['ground_mount_section'] else ''}

REQUIRED EMAIL STRUCTURE — follow this exactly, personalise the opening paragraph and
the goal statement based on the specific property and sunshine data above:

Dear {{company_name}} Team,

[Write a personalised 2-3 sentence opening paragraph that references the specific
address, the {kw['yearly_sunshine_hours']} annual sunshine hours, and why this
property is a good solar candidate. Make it feel specific, not generic.]

PROPERTY DETAILS:
─────────────────────────────────────
Address         : {kw['address']}
Roof Area       : {kw['roof_area_sqft']} sq ft ({kw['roof_area_m2']} m²)
Roof Installed  : {kw['roof_install_year']} ({kw['roof_age_years']} years old)
Annual Sunshine : {kw['yearly_sunshine_hours']} sunshine hours/year
Panel Potential : Up to {kw['max_panels']} panels based on roof assessment

ELECTRICITY CONSUMPTION:
─────────────────────────────────────
Average Monthly Bill    : ${kw['monthly_bill_usd']:,.0f}/month
Estimated Monthly Usage : ~{kw['monthly_consumption']} kWh/month

[If ground mount data provided, include it here as a separate section.
Ground mount data: {kw['ground_mount_section'] if kw['ground_mount_section'] else 'None — omit this section entirely.'}]

[Write a personalised 1-2 sentence goal statement based on the consumption data.]

I would appreciate if you could provide:
- Recommended system size (kW) for my consumption
- Panel brand and specifications
- Detailed pricing breakdown
- Available financing options (cash, loan, lease, PPA)
- Expected installation timeline
- Warranty terms for panels, inverter and workmanship
- Estimated annual energy production
- Any applicable local utility rebates or incentives

Please let me know if you require any additional information, such as electricity bills or roof photos, to prepare an initial estimate.

Thank you for your time, and I look forward to hearing from you.

Best regards,
{kw['homeowner_name']}
{kw['address']}
Date: {kw['today']}

---
This inquiry was assisted by Prometheus AI Solar Advisor

INSTRUCTIONS:
- Return ONLY the complete email text, no commentary before or after.
- Keep all section headers (PROPERTY DETAILS, ELECTRICITY CONSUMPTION) exactly as shown.
- Keep the separator lines (─────) exactly as shown.
- Do NOT include financial estimates (costs, savings, payback periods) anywhere in the email.
- Do NOT add or invent any data fields not listed above.
"""
        result = call_brain(prompt).strip()
        log.info("generate_rfp: Brain wrote %d-char personalised email", len(result))
        return result

    except Exception as exc:
        log.warning("generate_rfp: Brain call failed (%s) — using static template", exc)
        return _static_template(**kw)


def _static_template(**kw) -> str:
    """Original static template — used as fallback if Brain is unavailable."""
    ground = kw['ground_mount_section']
    return f"""Dear {kw['company_name']} Team,

I am exploring the possibility of installing a solar PV system for my residential property and would appreciate information on potential system sizing, pricing, and installation timelines.

PROPERTY DETAILS:
─────────────────────────────────────
Address         : {kw['address']}
Roof Area       : {kw['roof_area_sqft']} sq ft ({kw['roof_area_m2']} m²)
Roof Installed  : {kw['roof_install_year']} ({kw['roof_age_years']} years old)
Annual Sunshine : {kw['yearly_sunshine_hours']} sunshine hours/year
Panel Potential : Up to {kw['max_panels']} panels based on roof assessment

ELECTRICITY CONSUMPTION:
─────────────────────────────────────
Average Monthly Bill    : ${kw['monthly_bill_usd']:,.0f}/month
Estimated Monthly Usage : ~{kw['monthly_consumption']} kWh/month

My goal is to offset a significant portion of my electricity consumption through solar.
{ground}
I would appreciate if you could provide:
- Recommended system size (kW) for my consumption
- Panel brand and specifications
- Detailed pricing breakdown
- Available financing options (cash, loan, lease, PPA)
- Expected installation timeline
- Warranty terms for panels, inverter and workmanship
- Estimated annual energy production
- Any applicable local utility rebates or incentives

Please let me know if you require any additional information, such as electricity bills or roof photos, to prepare an initial estimate.

Thank you for your time, and I look forward to hearing from you.

Best regards,
{kw['homeowner_name']}
{kw['address']}
Date: {kw['today']}

---
This inquiry was assisted by Prometheus AI Solar Advisor
"""
