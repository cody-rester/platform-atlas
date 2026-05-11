"""
Endpoint planner — compute the minimum set of Platform API endpoints to
fetch for the active ruleset.

Continuous audit runs hourly; hammering Platform with the full collector
would be wasteful. This module walks every rule path in the active ruleset
once at startup and returns just the endpoints that some rule actually
consults. Endpoints with no consumers are skipped entirely.

Tradeoff: we only see drift in fields a rule covers. A config change in a
field nobody wrote a rule for won't alert. That's the correct design — the
ruleset is the explicit spec of what the customer cares about.
"""

from __future__ import annotations

import logging
from typing import Iterable

from platform_atlas.capture.collectors.platform import PLATFORM_API_ENDPOINTS

logger = logging.getLogger(__name__)


# Maps the second segment of a rule path under "platform.<X>" to the endpoint
# name in PLATFORM_API_ENDPOINTS. The reshape step in capture_engine.py uses
# the same mapping in reverse.
_PATH_TO_ENDPOINT: dict[str, str] = {
    "health_status":      "health_status",
    "health_server":      "health_server",
    "config":             "config",
    "adapter_status":     "adapter_status",
    "application_status": "application_status",
    "adapter_props":      "adapter_props",
    "application_props":  "application_props",
}

# Top-level keys that fall out of derived/post-processed data (adapters,
# applications, indexes_status). These don't map to a single fetch call;
# they require their underlying source endpoints to be present.
_DERIVED_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "adapters":    ("adapter_status", "adapter_props"),
    "applications": ("application_status", "application_props"),
    "indexes":      tuple(),  # special-cased: triggers per-collection index fetch
}


def _iter_rule_paths(ruleset: dict) -> Iterable[str]:
    """Yield every primary and alt path referenced by a ruleset."""
    for rule in ruleset.get("rules", []) or []:
        primary = rule.get("path")
        if primary:
            yield primary
        alt = rule.get("alt_path")
        if alt:
            yield alt


def required_endpoints(ruleset: dict) -> dict[str, str]:
    """Return the subset of PLATFORM_API_ENDPOINTS needed by the ruleset.

    Always includes ``health_status`` because the audit collector uses it to
    surface OAuth failures fast — it's a single tiny call regardless.

    Logs a single warning per ruleset for any malformed or unmapped
    ``platform.*`` paths so authors notice rules that will silently SKIP at
    validation time.
    """
    needed: set[str] = {"health_status"}
    needs_indexes = False
    malformed: list[str] = []
    unmapped: set[str] = set()

    for path in _iter_rule_paths(ruleset):
        parts = path.split(".")
        if not parts or parts[0] != "platform":
            # Non-platform path (mongo.*, redis.*, etc.) — irrelevant in
            # PLATFORM_ONLY mode. The rule will SKIP at validation time.
            continue
        if len(parts) < 2 or not parts[1]:
            malformed.append(path)
            continue
        section = parts[1]

        # Direct mapping (e.g. platform.config.* -> config endpoint)
        endpoint_name = _PATH_TO_ENDPOINT.get(section)
        if endpoint_name and endpoint_name in PLATFORM_API_ENDPOINTS:
            needed.add(endpoint_name)
            continue

        # Adapter / application derived sections
        if section in ("adapters", "applications"):
            for dep in _DERIVED_DEPENDENCIES.get(section, ()):
                if dep in PLATFORM_API_ENDPOINTS:
                    needed.add(dep)
            continue

        if section == "indexes_status":
            needs_indexes = True
            continue

        unmapped.add(section)

    if malformed:
        logger.warning(
            "Continuous audit: %d malformed platform.* path(s) skipped: %s",
            len(malformed), ", ".join(sorted(set(malformed))[:5]),
        )
    if unmapped:
        logger.warning(
            "Continuous audit: unmapped platform.* section(s) — rules will SKIP at validation: %s",
            ", ".join(sorted(unmapped)),
        )

    selected = {name: PLATFORM_API_ENDPOINTS[name] for name in needed}
    logger.debug(
        "Continuous audit endpoint plan: %d/%d endpoints (%s) · indexes=%s",
        len(selected), len(PLATFORM_API_ENDPOINTS), sorted(selected.keys()), needs_indexes,
    )
    return selected


def needs_index_status(ruleset: dict) -> bool:
    """Whether any rule references ``platform.indexes_status``."""
    for path in _iter_rule_paths(ruleset):
        parts = path.split(".")
        if len(parts) >= 2 and parts[0] == "platform" and parts[1] == "indexes_status":
            return True
    return False
