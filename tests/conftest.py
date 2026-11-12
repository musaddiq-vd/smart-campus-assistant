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
