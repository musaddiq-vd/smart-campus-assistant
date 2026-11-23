"""Shared test setup."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _no_question_log_in_app_database():
    """UI tests drive the chat without a temporary database, so the Insights log stays off.

    Tests of the log itself use temporary databases and switch it back on.

    The variable is set by hand, not with ``monkeypatch``: an autouse fixture that requests
    ``monkeypatch`` would set it up before every other fixture and so undo patches *after*
    fixtures such as ``fake_mode`` have restored their originals, leaving stale patches behind.
    """
    previous = os.environ.get("SCA_QUESTION_LOG")
    os.environ["SCA_QUESTION_LOG"] = "0"
    yield
    if previous is None:
        os.environ.pop("SCA_QUESTION_LOG", None)
    else:
        os.environ["SCA_QUESTION_LOG"] = previous


@pytest.fixture(autouse=True)
def _no_emails_from_ui_tests():
    """Page tests run admin actions against the app database: keep student emails off there too.

    Tests of the notifications themselves use temporary databases and switch them back on.
    Set by hand for the same reason as the question log above.
    """
    previous = os.environ.get("SCA_EMAIL_NOTIFICATIONS")
    os.environ["SCA_EMAIL_NOTIFICATIONS"] = "0"
    yield
    if previous is None:
        os.environ.pop("SCA_EMAIL_NOTIFICATIONS", None)
    else:
        os.environ["SCA_EMAIL_NOTIFICATIONS"] = previous


@pytest.fixture(autouse=True)
def _offline_demo_mode_by_default():
    """Tests are offline: run them in demo mode unless a test switches it off itself.

    Without this, a developer machine with AWS credentials would count as live mode, where
    one-time codes are never shown on screen, and the same tests would pass in CI but fail locally.
    """
    previous = os.environ.get("SCA_DEMO_MODE")
    os.environ["SCA_DEMO_MODE"] = "1"
    yield
    if previous is None:
        os.environ.pop("SCA_DEMO_MODE", None)
    else:
        os.environ["SCA_DEMO_MODE"] = previous


@pytest.fixture(autouse=True)
def _no_chat_question_limit_by_default():
    """The question budget is shared by the whole test process, so it is off unless a test turns it on."""
    previous = os.environ.get("SCA_CHAT_UNITS_PER_HOUR")
    os.environ["SCA_CHAT_UNITS_PER_HOUR"] = "0"
    yield
    if previous is None:
        os.environ.pop("SCA_CHAT_UNITS_PER_HOUR", None)
    else:
        os.environ["SCA_CHAT_UNITS_PER_HOUR"] = previous
