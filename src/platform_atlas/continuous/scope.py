"""
Continuous-audit capture scope guard.

Mirrors the tier-boundary defense pattern: any code path that opens an SSH
session, pymongo client, redis-py client, or kubectl process must call
``require_platform_only()`` in its constructor. If continuous-audit is
active, the call raises ``ScopeViolationError`` before any network I/O.

This is defense-in-depth: even if the endpoint planner accidentally asks for
something extended, the collector itself refuses to run.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from enum import Enum


class Scope(str, Enum):
    """The capture scope currently active in this process."""
    FULL = "full"                    # Normal capture path, no restriction
    PLATFORM_ONLY = "platform_only"  # Continuous audit, Platform OAuth only


class ScopeViolationError(RuntimeError):
    """Raised when a non-Platform collector is invoked under PLATFORM_ONLY."""


# Thread-local so a single Atlas process can run a normal capture in one
# thread and a continuous-audit poll in another without leaking the scope.
_state = threading.local()


def current_scope() -> Scope:
    return getattr(_state, "scope", Scope.FULL)


@contextmanager
def platform_only_scope():
    """Activate PLATFORM_ONLY for the duration of the context."""
    prev = current_scope()
    _state.scope = Scope.PLATFORM_ONLY
    try:
        yield
    finally:
        _state.scope = prev


def require_platform_only_compatible(component: str) -> None:
    """Call from any Extended-only collector's __init__ / from_config.

    Raises ScopeViolationError if the current scope forbids this component.
    The exception name + component is intentionally explicit so a stack
    trace points at the exact violator without further investigation.
    """
    if current_scope() == Scope.PLATFORM_ONLY:
        raise ScopeViolationError(
            f"{component} is not permitted under continuous-audit scope "
            f"(PLATFORM_ONLY). Continuous audit captures only Platform OAuth data."
        )


# Public alias used from collector code — short name keeps the call sites tidy.
require_platform_only = require_platform_only_compatible
