"""
Alert-policy filter for continuous audit.

``compute_drift`` always returns the full set of changed rules between two
runs. Storage of those events (``events.ndjson``) is unconditional — that's
the audit-grade timeline operators need to debug a regression weeks later.

What an operator sees in alerts.json + outbound notifications, however, is
a *filtered* subset, controlled by per-environment settings:

* ``alert_policy``
    - ``"any"``         (default): every drift event surfaces.
    - ``"regression"``  : only events where the rule transitioned PASS → FAIL.
                          Matches the operational "tell me when something that
                          was working just broke" use case.

* ``watchlist`` (rule-number allowlist)
    - empty       : every rule considered (default).
    - non-empty   : only events whose ``rule_number`` is in the list count.
                    Matching is case-insensitive; rule numbers are uppercased
                    on the way in by ``ContinuousSettings._coerce_watchlist``.

Filters compose: watchlist applies first, then policy. An event must satisfy
both to surface as an alert. The intent is that the watchlist narrows *which*
rules participate at all, while the policy describes *which kind of changes*
on those rules count.

This module is deliberately pure / no I/O — the engine reads settings, calls
``filter_drift_events_for_alerts``, then hands the result to ``alerts``.
Easy to unit-test, easy to reuse from the WebUI preview ("if I switch to
regression-only with this watchlist, what would have alerted last cycle?").
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from platform_atlas.continuous.models import (
    ALERT_POLICY_ANY,
    ALERT_POLICY_REGRESSION,
    DEFAULT_ALERT_POLICY,
    VALID_ALERT_POLICIES,
)

logger = logging.getLogger(__name__)


def _normalize_rule_number(value: Any) -> str:
    """Uppercase + strip a rule-number value for comparison."""
    if value is None:
        return ""
    return str(value).strip().upper()


def _is_pass_to_fail(event: dict[str, Any]) -> bool:
    """True when a drift event is a PASS → FAIL transition.

    ``compute_drift`` populates ``previous_status`` and ``current_status`` on
    every event, both already upper-cased. We treat anything other than the
    exact pair ``(PASS, FAIL)`` as not-a-regression — including ERROR cases,
    WARN, INFO, etc. Operators who want a broader definition can switch back
    to ``alert_policy = any``.
    """
    return (
        str(event.get("previous_status", "")).upper() == "PASS"
        and str(event.get("current_status", "")).upper() == "FAIL"
    )


def filter_drift_events_for_alerts(
    events: Iterable[dict[str, Any]],
    *,
    policy: str = DEFAULT_ALERT_POLICY,
    watchlist: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter drift events down to the subset that should fire alerts.

    The returned list preserves input order (stable per cycle). Inputs are
    not mutated. Unknown ``policy`` values fall back to the default and emit
    a single warning per call.

    Args:
        events: drift events as produced by ``compute_drift`` (each carries
            ``rule_number``, ``previous_status``, ``current_status``, …).
        policy: ``"any"`` or ``"regression"``. Anything else falls back to
            the default with a warning.
        watchlist: optional iterable of rule numbers. Matching is
            case-insensitive. ``None`` or empty means "no filter".

    Returns:
        A new list of events (subset of input) ready to feed into
        ``update_alert_state``.
    """
    if policy not in VALID_ALERT_POLICIES:
        logger.warning(
            "Unknown alert_policy %r; falling back to %r",
            policy, DEFAULT_ALERT_POLICY,
        )
        policy = DEFAULT_ALERT_POLICY

    watchset: set[str] = {
        _normalize_rule_number(item) for item in (watchlist or ()) if str(item).strip()
    }

    out: list[dict[str, Any]] = []
    for event in events:
        rule_number = _normalize_rule_number(event.get("rule_number"))
        if not rule_number:
            # Defensive — drift events without a rule_number can't be
            # alerted on usefully and would also break Alert keying.
            continue
        if watchset and rule_number not in watchset:
            continue
        if policy == ALERT_POLICY_REGRESSION and not _is_pass_to_fail(event):
            continue
        out.append(event)
    return out


def describe_policy(policy: str) -> str:
    """Human-readable label for a policy value (used by CLI/WebUI display)."""
    return {
        ALERT_POLICY_ANY:        "Any change",
        ALERT_POLICY_REGRESSION: "Regression only (PASS → FAIL)",
    }.get(policy, policy)
