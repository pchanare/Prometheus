"""
send_all_rfps_tool.py — Composite tool that generates and sends RFP emails to
all 3 installers in a single model tool call.

Replaces the 6-step sequence:
  generate_rfp(c1) → send_rfp_email(c1) →
  generate_rfp(c2) → send_rfp_email(c2) →
  generate_rfp(c3) → send_rfp_email(c3)

In Gemini Live native audio, calling those six tools sequentially causes the
model to generate audio after each result — six segments of speech within one
turn.  This composite runs all six internally and returns one combined status
dict, so the model calls it once, stays silent while all emails are sent, and
confirms with a single response.
"""

import logging

log = logging.getLogger("prometheus.send_all_rfps_tool")


def send_all_rfps(
    address: str,
    homeowner_name: str,
    roof_age_years: int,
    monthly_bill_usd: float,
    yearly_sunshine_hours: float,
    max_panels: int,
    roof_area_m2: float,
    company1_name: str,
    company1_email: str,
    company2_name: str,
    company2_email: str,
    company3_name: str,
    company3_email: str,
) -> dict:
    """
    Generate and send personalised RFP emails to all 3 solar installers.

    Call this ONLY after the user has explicitly confirmed they want emails
    sent and you have the company names and emails from find_local_installers.
    Do NOT call generate_rfp or send_rfp_email individually — use this tool.

    Args:
        address:                Full property address.
        homeowner_name:         Homeowner's name (confirmed earlier in this session).
        roof_age_years:         Age of the roof in years.
        monthly_bill_usd:       Average monthly electricity bill in USD.
        yearly_sunshine_hours:  Annual peak sunshine hours (from run_solar_analysis).
        max_panels:             Maximum panels the roof can fit (from run_solar_analysis).
        roof_area_m2:           Usable roof area in m² (from run_solar_analysis).
        company1_name:          Name of installer 1 (from find_local_installers).
        company1_email:         Email of installer 1.
        company2_name:          Name of installer 2.
        company2_email:         Email of installer 2.
        company3_name:          Name of installer 3.
        company3_email:         Email of installer 3.

    Returns:
        Dict summarising the send status for all 3 emails:
          total_sent, total_failed
          results: list of {company_name, company_email, status, message}
    """
    from rfp_generator import generate_rfp
    from send_rfp_email import send_rfp_email

    installers = [
        (company1_name, company1_email),
        (company2_name, company2_email),
        (company3_name, company3_email),
    ]

    results = []
    sent    = 0
    failed  = 0

    for company_name, company_email in installers:
        # Generate the RFP (stores email body server-side)
        try:
            gen_result = generate_rfp(
                address               = address,
                yearly_sunshine_hours = float(yearly_sunshine_hours or 0),
                max_panels            = int(max_panels or 0),
                roof_area_m2          = float(roof_area_m2 or 0),
                roof_age_years        = int(roof_age_years or 0),
                monthly_bill_usd      = float(monthly_bill_usd or 0),
                homeowner_name        = homeowner_name,
                company_name          = company_name,
            )
            if gen_result.get("status") != "success":
                raise RuntimeError(f"generate_rfp returned {gen_result}")
        except Exception as exc:
            log.error("send_all_rfps: generate_rfp failed for %r: %s", company_name, exc)
            failed += 1
            results.append({
                "company_name":  company_name,
                "company_email": company_email,
                "status":        "failed",
                "message":       f"RFP generation failed: {exc}",
            })
            continue

        # Send the email
        try:
            send_result = send_rfp_email(
                company_name   = company_name,
                company_email  = company_email,
                homeowner_name = homeowner_name,
            )
            status = send_result.get("status", "failed")
            if status in ("success", "already_sent"):
                sent += 1
            else:
                failed += 1
            results.append({
                "company_name":  company_name,
                "company_email": company_email,
                "status":        status,
                "message":       send_result.get("message", ""),
            })
        except Exception as exc:
            log.error("send_all_rfps: send_rfp_email failed for %r: %s", company_name, exc)
            failed += 1
            results.append({
                "company_name":  company_name,
                "company_email": company_email,
                "status":        "failed",
                "message":       f"Email send failed: {exc}",
            })

    log.info("send_all_rfps: sent=%d, failed=%d", sent, failed)

    return {
        "total_sent":   sent,
        "total_failed": failed,
        "results":      results,
        "summary": (
            f"All 3 RFP emails sent successfully to {company1_name}, "
            f"{company2_name}, and {company3_name}."
            if failed == 0 else
            f"{sent} of 3 emails sent; {failed} failed. See results for details."
        ),
    }
