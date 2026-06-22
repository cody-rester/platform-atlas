"""Authenticated Platform client for the artifact engine.

Thin accessor around the existing ``PlatformCollector`` so the artifact reader
and exporter share one OAuth'd ipsdk client. The client is cached per active
environment (URI + client_id) — the ipsdk client caches its OAuth token, so
repeated browse calls (search-as-you-type) don't re-authenticate every time.

Creation is guarded by a lock: the WebUI asset picker fires several list
requests in parallel (one per tab), each of which would otherwise build the
client — and run the OS-keyring probe — at the same time. Concurrent keyring
probes race on a shared probe key and spuriously report the keyring as broken,
which surfaced as intermittent 502s that cleared once the cache warmed. Building
exactly one client at a time removes the race; the other callers reuse the cache.
"""
from __future__ import annotations

import threading
from typing import Any

from platform_atlas.capture.collectors.platform import PlatformCollector
from platform_atlas.core.context import ctx

_CACHE: dict[str, Any] = {}
_LOCK = threading.Lock()


def _close_quietly(client: Any) -> None:
    """Close a cached ipsdk client's HTTP connection pool, ignoring errors.

    Mirrors ``PlatformCollector.close()`` so rebuilding or clearing the cache
    (env switch / ``force_new``) doesn't leak the previous environment's httpx
    connection pool.
    """
    try:
        if client is not None and hasattr(client, "client"):
            client.client.close()
    except Exception:  # noqa: BLE001 — close is best-effort
        pass


def get_client(*, force_new: bool = False) -> Any:
    """Return an authenticated ipsdk Platform client for the active environment.

    Reuses a cached client while the active environment's ``platform_uri`` and
    ``platform_client_id`` are unchanged; rebuilds it (re-authenticating) when
    the environment changes or ``force_new`` is set. Thread-safe: concurrent
    callers serialize on first build, then share the cached client.
    """
    cfg = ctx().config
    key = f"{cfg.platform_uri}|{cfg.platform_client_id}"
    with _LOCK:
        if force_new or key not in _CACHE:
            for old in _CACHE.values():
                _close_quietly(old)
            _CACHE.clear()
            _CACHE[key] = PlatformCollector.from_config()._client
        return _CACHE[key]


def reset_client_cache() -> None:
    """Drop the cached client (e.g. after an environment switch)."""
    with _LOCK:
        for old in _CACHE.values():
            _close_quietly(old)
        _CACHE.clear()
