from datetime import datetime

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
    Generate a professional solar installation inquiry email.

    Args:
        address: Property address
        yearly_sunshine_hours: Annual sunshine hours
        max_panels: Maximum number of panels
        roof_area_m2: Roof area in square meters
        roof_age_years: Age of roof in years
        monthly_bill_usd: Average monthly electricity bill
        homeowner_name: Name of the homeowner
        ground_mount_analysis: Analysis of ground mount potential
        company_name: Name of the installation company

    Returns:
        Dict with email content
    """
    today = datetime.now().strftime("%B %d, %Y")
    roof_area_sqft = round(roof_area_m2 * 10.764, 1)
    roof_install_year = datetime.now().year - roof_age_years
    monthly_consumption_estimate = round(monthly_bill_usd / 0.12)

    address_parts = address.split(",")
    city_state = ", ".join(address_parts[1:]).strip() if len(address_parts) > 1 else address

    ground_mount_section = ""
    if ground_mount_analysis != "Not provided":
        ground_mount_section = f"""
I am also interested in exploring ground-mounted solar options for my 
outdoor space. Based on a preliminary assessment of my property:

{ground_mount_analysis}

Please include ground-mounted system options in your proposal if applicable.
"""

    email_content = f"""Dear {company_name} Team,

I am exploring the possibility of installing a solar PV system for my residential property and would appreciate information on potential system sizing, pricing, and installation timelines.

PROPERTY DETAILS:
─────────────────────────────────────
Address         : {address}
Roof Area       : {roof_area_sqft} sq ft ({roof_area_m2} m²)
Roof Installed  : {roof_install_year} ({roof_age_years} years old)
Annual Sunshine : {yearly_sunshine_hours} sunshine hours/year
Panel Potential : Up to {max_panels} panels based on roof assessment

ELECTRICITY CONSUMPTION:
─────────────────────────────────────
Average Monthly Bill    : ${monthly_bill_usd:,.0f}/month
Estimated Monthly Usage : ~{monthly_consumption_estimate} kWh/month

My goal is to offset a significant portion of my electricity consumption through solar.
{ground_mount_section}
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
{homeowner_name}
{address}
Date: {today}

---
This inquiry was assisted by Prometheus AI Solar Advisor
"""

    return {
        "status": "success",
        "email_content": email_content,
        "subject": f"Solar Installation Inquiry - {city_state}",
        "address": address,
        "date": today,
        "company_name": company_name
    }