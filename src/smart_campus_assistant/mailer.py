"""Outgoing email over SMTP (STARTTLS), used for password reset codes.

Configured only through environment variables (never commit values):
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
``send_email`` never raises and never logs secrets or message bodies.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)

SMTP_VARS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
TIMEOUT_SECONDS = 10

RESET_SUBJECT = "XYZ College helpdesk: your password reset code"


def reset_email_body(code: str, minutes: int) -> str:
    """Plain-text password reset email."""
    return (
        "Hello,\n\n"
        "Someone asked to reset the password of your XYZ College helpdesk account.\n\n"
        f"Your reset code is: {code}\n\n"
        f"The code is valid for {minutes} minutes and can be tried at most 5 times.\n"
        "If you did not ask for this, you can ignore this email; your password stays the same.\n\n"
        "XYZ College of Engineering (fictional demo)\n"
    )


def email_configured() -> bool:
    """True when every SMTP_* variable is set and SMTP_PORT is a number."""
    if not all((os.getenv(name) or "").strip() for name in SMTP_VARS):
        return False
    return os.getenv("SMTP_PORT", "").strip().isdigit()


def send_email(to: str, subject: str, body: str) -> bool:
    """Send one plain-text email; True on success, False on any problem (never raises)."""
    if not email_configured():
        return False
    try:
        msg = EmailMessage()
        msg["From"] = os.environ["SMTP_FROM"].strip()
        msg["To"] = str(to).strip()
        msg["Subject"] = subject
        msg.set_content(body)
        host, port = os.environ["SMTP_HOST"].strip(), int(os.environ["SMTP_PORT"].strip())
        with smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(os.environ["SMTP_USER"].strip(), os.environ["SMTP_PASSWORD"])
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001  (callers only need ok / not ok)
        log.warning("Sending email failed (%s)", type(exc).__name__)
        return False
