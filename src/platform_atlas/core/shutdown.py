"""
Cooperative shutdown registry for graceful SIGINT handling.

Register cleanup callables with register_cleanup() at startup.
The SIGINT handler calls request_shutdown() — safe from signal context.
The main thread polls shutdown_requested() and calls run_cleanups() from
normal (non-signal) context to avoid paramiko/pymongo deadlocks.
"""
from __future__ import annotations

import logging
from threading import Event
from typing import Callable

logger = logging.getLogger(__name__)

_shutdown_event: Event = Event()
_cleanups: dict[str, Callable[[], None]] = {}


def register_cleanup(name: str, fn: Callable[[], None]) -> None:
    """Register a cleanup callable to run on graceful shutdown."""
    _cleanups[name] = fn


def unregister_cleanup(name: str) -> None:
    """Remove a previously registered cleanup callable."""
    _cleanups.pop(name, None)


def request_shutdown() -> None:
    """Signal that a graceful shutdown is requested (safe from signal context)."""
    _shutdown_event.set()


def shutdown_requested() -> bool:
    """Return True if a shutdown has been requested."""
    return _shutdown_event.is_set()


def run_cleanups() -> None:
    """Run all registered cleanup callbacks; individual failures are isolated."""
    for name, fn in list(_cleanups.items()):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Cleanup %r raised: %s", name, exc)
