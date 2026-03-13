import os
import base64
import pickle
import time
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.pickle")

# Deduplication guard — prevents the same email being sent twice within a short
# window even if the model calls send_rfp_email more than once (e.g. due to
# context replay).  Key: "<company_email>::<subject>", value: unix timestamp.
_DEDUP_WINDOW_S = 120   # 2-minute window
_sent_log: dict[str, float] = {}


def send_rfp_email(
    company_name: str,
    company_email: str,
    homeowner_name: str,
) -> dict:
    """
    Send the RFP email prepared by generate_rfp to a solar installation company
    via Gmail.  The email body and subject are retrieved automatically from the
    server-side store — do NOT pass them here; they are never sent to the model.

    Call generate_rfp for this company BEFORE calling this function.

    Args:
        company_name:   Name of the solar company (must match the name passed to
                        generate_rfp so the stored email can be found).
        company_email:  Recipient email address.
        homeowner_name: Name of the homeowner (used in logging / confirmation).

    Returns:
        Dict with send status.
    """
    try:
        from status_channel import push_status as _push_status
        _push_status(f"📧 Sending email to {company_name}…")
    except Exception:
        pass

    # ── Retrieve stored email content ────────────────────────────────────────
    from rfp_generator import _rfp_store
    stored = _rfp_store.get(company_name)
    if not stored:
        return {
            "status":       "failed",
            "company_name": company_name,
            "error":        f"No RFP found for {company_name!r}. Call generate_rfp first.",
            "message":      f"Cannot send — no RFP has been generated for {company_name}.",
        }
    subject       = stored["subject"]
    email_content = stored["email_content"]

    # ── Deduplication guard ──────────────────────────────────────────────────
    dedup_key = f"{company_email}::{subject}"
    now = time.time()
    if dedup_key in _sent_log and now - _sent_log[dedup_key] < _DEDUP_WINDOW_S:
        return {
            "status": "already_sent",
            "company_name": company_name,
            "company_email": company_email,
            "message": f"Email to {company_name} was already sent moments ago — skipping duplicate.",
        }
    _sent_log[dedup_key] = now
    # ────────────────────────────────────────────────────────────────────────

    try:
        sender_email = os.environ.get("SENDER_EMAIL", "me")

        if not os.path.exists(TOKEN_FILE):
            return {
                "status": "failed",
                "error": "token.pickle not found. Please run auth_test.py first.",
                "message": "Authentication token missing"
            }

        with open(TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "wb") as token:
                pickle.dump(creds, token)

        service = build("gmail", "v1", credentials=creds)

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: Arial, sans-serif;
            font-size: 15px;
            color: #222;
            max-width: 650px;
            margin: auto;
            padding: 20px;
        }}
        .header {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            padding: 30px 20px;
            border-radius: 8px 8px 0 0;
            text-align: center;
        }}
        .header h1 {{
            color: white;
            margin: 0;
            font-size: 24px;
        }}
        .header p {{
            color: white;
            margin: 5px 0 0 0;
            font-size: 13px;
        }}
        .content {{
            background-color: #ffffff;
            padding: 25px;
            border: 1px solid #e0e0e0;
        }}
        .section {{
            background-color: #f9f9f9;
            border-left: 4px solid #f4a800;
            padding: 12px 16px;
            margin: 16px 0;
            border-radius: 0 6px 6px 0;
        }}
        .section h3 {{
            margin: 0 0 8px 0;
            color: #f4a800;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .section p {{
            margin: 4px 0;
            font-size: 14px;
            color: #444;
        }}
        ul {{
            padding-left: 20px;
            color: #444;
            font-size: 14px;
        }}
        ul li {{
            margin-bottom: 6px;
        }}
        .footer {{
            background-color: #f4f4f4;
            padding: 15px;
            text-align: center;
            font-size: 12px;
            color: #888;
            border-radius: 0 0 8px 8px;
            border: 1px solid #e0e0e0;
            border-top: none;
        }}
        .footer span {{
            color: #f4a800;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>☀️ Solar Installation Inquiry</h1>
        <p>Powered by Prometheus AI Solar Advisor</p>
    </div>
    <div class="content">
        {email_content.replace(chr(10), '<br>').replace('─────────────────────────────────────', '<hr style="border:1px solid #eee;">')}
    </div>
    <div class="footer">
        This email was generated by <span>Prometheus AI Solar Advisor</span><br>
        Helping homeowners go solar — faster and smarter.
    </div>
</body>
</html>
"""

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["To"] = company_email
        message["From"] = sender_email

        plain_part = MIMEText(email_content, "plain")
        html_part = MIMEText(html_content, "html")

        message.attach(plain_part)
        message.attach(html_part)

        encoded_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode("utf-8")

        send_result = service.users().messages().send(
            userId="me",
            body={"raw": encoded_message}
        ).execute()

        return {
            "status": "success",
            "company_name": company_name,
            "company_email": company_email,
            "message_id": send_result.get("id"),
            "message": f"Email successfully sent to {company_name} at {company_email}"
        }

    except Exception as e:
        return {
            "status": "failed",
            "company_name": company_name,
            "company_email": company_email,
            "error": str(e),
            "message": f"Failed to send email to {company_name}: {str(e)}"
        }