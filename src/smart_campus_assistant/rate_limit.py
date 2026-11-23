"""Per-account question budget for the "Ask" page, so one account cannot run up the model bill.

Each answered question costs units in a sliding one-hour window: a simple or personal answer
(about 2 model calls) costs 1 unit, a deep research report (about 10 calls) costs
``DEEP_UNITS``. When an account's units in the last hour reach the budget, it has to wait
until the oldest of them is an hour old.

The budget is ``SCA_CHAT_UNITS_PER_HOUR`` (default 40; ``0`` turns the limit off). It is kept
in memory, shared by every browser session of the server process (Streamlit runs one), so
opening more tabs does not give more questions. A restart starts every budget afresh.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Callable

DEFAULT_UNITS_PER_HOUR = 40
DEEP_UNITS = 4
WINDOW_SECONDS = 3600.0


def units_for(route: str | None) -> int:
    """Units one answered question costs on ``route``."""
    return DEEP_UNITS if route == "deep" else 1


def units_per_hour() -> int:
    """The configured budget (0 = unlimited); a bad value falls back to the default."""
    try:
        return max(0, int(os.getenv("SCA_CHAT_UNITS_PER_HOUR", str(DEFAULT_UNITS_PER_HOUR)).strip()))
    except ValueError:
        return DEFAULT_UNITS_PER_HOUR


class QuestionBudget:
    """Sliding-window budget of units per key (the account email). Thread-safe."""

    def __init__(self, units: int | None = None, window: float = WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fixed_units = None if units is None else max(0, int(units))
        self.window = window
        self.clock = clock
        self._spent: dict[str, deque[tuple[float, int]]] = {}
        self._lock = threading.Lock()

    @property
    def units(self) -> int:
        """The budget: fixed at creation, else read from the environment on every check."""
        return units_per_hour() if self._fixed_units is None else self._fixed_units

    def _prune(self, key: str, now: float) -> deque[tuple[float, int]]:
        spent = self._spent.setdefault(key, deque())
        while spent and now - spent[0][0] >= self.window:
            spent.popleft()
        return spent

    def used(self, key: str) -> int:
        with self._lock:
            return sum(units for _, units in self._prune(str(key).lower(), self.clock()))

    def wait_seconds(self, key: str) -> float:
        """0.0 when ``key`` may ask now, else the seconds until enough units are free again."""
        if not self.units:
            return 0.0
        with self._lock:
            now = self.clock()
            spent = self._prune(str(key).lower(), now)
            total = sum(units for _, units in spent)
            if total < self.units:
                return 0.0
            # Free units oldest first until the account is below its budget again.
            for stamp, units in spent:
                total -= units
                if total < self.units:
                    return max(0.0, stamp + self.window - now)
            return 0.0  # pragma: no cover  (the loop always returns)

    def record(self, key: str, units: int) -> None:
        """Count ``units`` for ``key`` now."""
        if not self.units or units <= 0:
            return
        with self._lock:
            now = self.clock()
            self._prune(str(key).lower(), now).append((now, int(units)))


def wait_message(seconds: float) -> str:
    minutes = max(1, int(-(-seconds // 60)))  # round up
    return (f"You have asked a lot of questions in the last hour. Please try again in about {minutes} "
            f"minute{'s' if minutes != 1 else ''}. (Each deep research report counts as {DEEP_UNITS} questions.)")
