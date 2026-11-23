"""Outgoing email over SMTP (STARTTLS), used for password reset codes and student notifications.

Configured only through environment variables (never commit values):
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.
``send_email`` never raises and never logs secrets or message bodies.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from contextlib import contextmanager
from email.message import EmailMessage
from typing import Callable, Iterator

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


def _message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"].strip()
    msg["To"] = str(to).strip()
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _connect() -> smtplib.SMTP:
    """An SMTP connection that is already secured with STARTTLS and logged in."""
    host, port = os.environ["SMTP_HOST"].strip(), int(os.environ["SMTP_PORT"].strip())
    smtp = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
    try:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(os.environ["SMTP_USER"].strip(), os.environ["SMTP_PASSWORD"])
    except Exception:
        _close(smtp)
        raise
    return smtp


def _close(smtp) -> None:
    for method in ("quit", "close"):
        try:
            getattr(smtp, method)()
            return
        except Exception:  # noqa: BLE001,S112
            continue


def send_email(to: str, subject: str, body: str) -> bool:
    """Send one plain-text email; True on success, False on any problem (never raises)."""
    if not email_configured():
        return False
    try:
        msg = _message(to, subject, body)
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


@contextmanager
def smtp_session() -> Iterator[Callable[[str, str, str], bool]]:
    """Yield ``send(to, subject, body) -> bool`` that reuses ONE SMTP connection for many emails.

    A notice to many students then needs one login instead of one per email. After any error
    the connection is dropped and the next email opens a fresh one. ``send`` never raises.
    """
    state: dict = {"smtp": None}

    def send(to: str, subject: str, body: str) -> bool:
        if not email_configured():
            return False
        try:
            if state["smtp"] is None:
                state["smtp"] = _connect()
            state["smtp"].send_message(_message(to, subject, body))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Sending email failed (%s)", type(exc).__name__)
            if state["smtp"] is not None:
                _close(state["smtp"])
            state["smtp"] = None
            return False

    try:
        yield send
    finally:
        if state["smtp"] is not None:
            _close(state["smtp"])
