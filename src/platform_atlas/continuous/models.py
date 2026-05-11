"""Data models for the continuous-audit subsystem."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# Default schedule: hourly. Configurable per-environment.
DEFAULT_INTERVAL_SECONDS = 3600

# Default retention: 7 days at hourly cadence = 168 runs.
DEFAULT_RETAIN_RUNS = 168

# Alert policy values. ``any`` = current behavior (alert on any drifted rule);
# ``regression`` = alert only when a rule transitions from PASS → FAIL. Both
# operate on top of the watchlist when one is set. Defaults preserve pre-1.7.x
# behavior on upgrade — empty watchlist + ``any`` policy = identical to before.
ALERT_POLICY_ANY = "any"
ALERT_POLICY_REGRESSION = "regression"
VALID_ALERT_POLICIES = (ALERT_POLICY_ANY, ALERT_POLICY_REGRESSION)
DEFAULT_ALERT_POLICY = ALERT_POLICY_ANY


def _coerce_int(raw: Any, default: int) -> int:
    if isinstance(raw, bool):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _coerce_alert_policy(raw: Any) -> str:
    """Normalize an alert-policy value, falling back to the default on garbage."""
    if not isinstance(raw, str):
        return DEFAULT_ALERT_POLICY
    candidate = raw.strip().lower()
    if candidate in VALID_ALERT_POLICIES:
        return candidate
    logger.warning(
        "Unknown continuous_audit.alert_policy %r — falling back to %r",
        raw, DEFAULT_ALERT_POLICY,
    )
    return DEFAULT_ALERT_POLICY


def _coerce_watchlist(raw: Any) -> tuple[str, ...]:
    """Normalize a watchlist value into a deduped tuple of uppercase rule numbers.

    Accepts a list/tuple of strings on disk. Trims whitespace, uppercases
    (rule numbers in this codebase are conventionally upper-case), drops
    empties, dedupes while preserving first-seen order.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        # Tolerate a single string for forgiveness — comma-or-space split.
        items = [t for t in raw.replace(",", " ").split() if t]
    elif isinstance(raw, (list, tuple)):
        items = [str(t) for t in raw]
    else:
        logger.warning(
            "continuous_audit.watchlist is not a list (got %s); ignoring",
            type(raw).__name__,
        )
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        norm = item.strip().upper()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return tuple(out)


@dataclass(frozen=True)
class ContinuousSettings:
    """Per-environment continuous-audit configuration.

    Persisted on the environment overlay file under the key
    ``continuous_audit``. Reading is centralized in ``runtime.read_settings``
    so callers never parse the JSON themselves.
    """
    enabled: bool = False
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    retain_runs: int = DEFAULT_RETAIN_RUNS
    ruleset_id: str = ""
    profile_id: str = ""
    # Alert policy: which drift events should produce an alert / notification.
    # Storage of the underlying drift history is unaffected (events.ndjson
    # always contains everything detected). See continuous/policy.py.
    alert_policy: str = DEFAULT_ALERT_POLICY
    # Optional rule-number allowlist. When non-empty, only drift events
    # whose ``rule_number`` matches one of these (case-insensitive) trigger
    # an alert. Empty = no filter (all rules considered, current behavior).
    watchlist: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # Persist watchlist as a list (JSON has no tuple type) so the env
        # file round-trips cleanly under any external editor.
        out["watchlist"] = list(self.watchlist)
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "ContinuousSettings":
        if not data:
            return cls()
        if not isinstance(data, dict):
            logger.warning("continuous_audit settings is not a JSON object (got %s); using defaults",
                           type(data).__name__)
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            interval_seconds=_coerce_int(data.get("interval_seconds"), DEFAULT_INTERVAL_SECONDS),
            retain_runs=_coerce_int(data.get("retain_runs"), DEFAULT_RETAIN_RUNS),
            ruleset_id=str(data.get("ruleset_id") or ""),
            profile_id=str(data.get("profile_id") or ""),
            alert_policy=_coerce_alert_policy(data.get("alert_policy")),
            watchlist=_coerce_watchlist(data.get("watchlist")),
        )


@dataclass
class RunSummary:
    """Roll-up of one continuous-audit run's outcome."""
    pass_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    error_count: int = 0
    drifted_count: int = 0

    @property
    def total(self) -> int:
        return self.pass_count + self.fail_count + self.skip_count + self.error_count

    def to_dict(self) -> dict[str, int]:
        out = asdict(self)
        out["total"] = self.total
        return out


@dataclass
class RunResult:
    """One continuous-audit run, ready to write to disk as JSON."""
    run_id: str                         # ISO timestamp + env, e.g. 2026-05-04T18-00-00Z-prod
    environment: str                    # env name, "" if no active env
    started_at: str                     # ISO 8601 UTC
    finished_at: str                    # ISO 8601 UTC
    duration_ms: int
    ruleset_id: str
    ruleset_version: str
    summary: RunSummary = field(default_factory=RunSummary)
    # Each result carries: rule_number, name, severity, status, path, expected, actual, message,
    # plus a "drift" sub-dict when the value changed from the previous run.
    results: list[dict[str, Any]] = field(default_factory=list)
    capture_error: str | None = None    # Set when capture itself failed (no validation possible).
    # True when a previous run existed on disk but could not be read (corrupt
    # JSON, broken symlink, permission flake). Drift detection skipped this run.
    previous_unreadable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "environment": self.environment,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "ruleset": {"id": self.ruleset_id, "version": self.ruleset_version},
            "summary": self.summary.to_dict(),
            "results": self.results,
            "capture_error": self.capture_error,
            "previous_unreadable": self.previous_unreadable,
        }


class AlertStatus(str, Enum):
    UNACKED = "unacked"
    ACKED = "acked"


@dataclass
class DriftEvent:
    """A single observed-value change between two consecutive runs."""
    rule_number: str
    rule_name: str
    severity: str
    path: str
    previous: Any
    current: Any
    previous_run_id: str
    current_run_id: str
    detected_at: str  # ISO 8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Alert:
    """An aggregated, user-facing alert backed by one or more drift events.

    One alert per (rule_number, path). Repeated drift on the same rule path
    bumps ``occurrence_count`` and ``last_seen`` rather than creating a new
    alert — the timeline is in events.ndjson; this file is the dashboard view.
    """
    alert_id: str               # Stable: hash of rule_number + path
    rule_number: str
    rule_name: str
    severity: str
    path: str
    first_seen: str             # ISO 8601 UTC
    last_seen: str              # ISO 8601 UTC
    occurrence_count: int
    latest_previous: Any
    latest_current: Any
    status: AlertStatus = AlertStatus.UNACKED
    acked_at: str | None = None
    acked_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["status"] = self.status.value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alert":
        status = AlertStatus(data.get("status", AlertStatus.UNACKED.value))
        return cls(
            alert_id=data["alert_id"],
            rule_number=data["rule_number"],
            rule_name=data["rule_name"],
            severity=data["severity"],
            path=data["path"],
            first_seen=data["first_seen"],
            last_seen=data["last_seen"],
            occurrence_count=int(data.get("occurrence_count", 1)),
            latest_previous=data.get("latest_previous"),
            latest_current=data.get("latest_current"),
            status=status,
            acked_at=data.get("acked_at"),
            acked_by=data.get("acked_by"),
        )
