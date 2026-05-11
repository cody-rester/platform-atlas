"""
Alert state — aggregates drift events into a per-(rule, path) record with
ack support.

Two files:
    events.ndjson    — append-only timeline (handled by storage.py).
    alerts.json      — current state, one entry per (rule_number, path).

When a drift event arrives:
    - if no alert exists → create one (UNACKED, occurrence_count=1)
    - if an alert exists and is UNACKED → bump occurrence_count, refresh last_seen
    - if an alert exists and is ACKED → re-open it (ACKED → UNACKED) because
      the value drifted again after we said "I've seen this"

Alerts are never auto-deleted; ops can clear them by acknowledging.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from platform_atlas.continuous import storage
from platform_atlas.continuous.models import Alert, AlertStatus

logger = logging.getLogger(__name__)


def _alert_id(rule_number: str, path: str) -> str:
    """Stable identifier so the same drift always lands in the same alert."""
    raw = f"{rule_number}|{path}".encode("utf-8")
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]


def _read_alerts_dict(environment: str) -> dict[str, Alert]:
    path = storage.alerts_path(environment)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Preserve the corrupt file aside before treating this as an empty
        # state. The next write would otherwise silently overwrite the only
        # copy of historical alerts. Operators can recover by renaming the
        # ``.corrupt-<ts>`` file back into place.
        logger.warning("Alerts file unreadable for env=%s: %s — preserving as .corrupt", environment, exc)
        try:
            corrupt_path = path.with_name(f"{path.name}.corrupt-{storage.now_iso().replace(':', '-')}")
            path.rename(corrupt_path)
        except OSError as rename_exc:
            logger.warning("Could not rename corrupt alerts file aside: %s", rename_exc)
        return {}
    out: dict[str, Alert] = {}
    for entry in raw.get("alerts", []) or []:
        try:
            alert = Alert.from_dict(entry)
            out[alert.alert_id] = alert
        except (KeyError, ValueError) as exc:
            logger.debug("Skipping malformed alert entry: %s", exc)
    return out


def _write_alerts_dict(environment: str, alerts: dict[str, Alert]) -> None:
    path = storage.alerts_path(environment)
    payload = {
        "alerts": [alerts[k].to_dict() for k in sorted(alerts.keys())],
        "counts": _counts(alerts.values()),
    }
    storage._atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))  # pylint: disable=protected-access


def _counts(alerts) -> dict[str, int]:
    total = 0
    unacked = 0
    by_severity: dict[str, int] = {}
    for a in alerts:
        total += 1
        if a.status == AlertStatus.UNACKED:
            unacked += 1
        sev = (a.severity or "unknown").lower()
        by_severity[sev] = by_severity.get(sev, 0) + 1
    return {"total": total, "unacked": unacked, "by_severity": by_severity}


def update_alert_state(environment: str, drift_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply a batch of drift events to the alerts.json file.

    Holds an exclusive flock around the read-modify-write window so concurrent
    invocations (WebUI scheduler vs. systemd timer vs. CLI run-once) cannot
    lose transitions. Always rewrites the file (even when no events) so
    counts/timestamps stay fresh.

    Returns the subset of ``drift_events`` that caused an alert *transition*
    (newly-created alert or re-opened acked alert). This is the right input
    for outbound notification dispatch — drift on an already-unacked alert is
    already on the dashboard and re-firing every cycle would be spam.
    """
    transitions: list[dict[str, Any]] = []
    with storage.alerts_lock(environment):
        alerts = _read_alerts_dict(environment)

        for event in drift_events:
            rule_number = str(event.get("rule_number", ""))
            path = str(event.get("path", ""))
            if not rule_number:
                logger.debug("Skipping drift event with no rule_number (path=%s)", path)
                continue

            alert_id = _alert_id(rule_number, path)
            existing = alerts.get(alert_id)
            detected = str(event.get("detected_at", ""))

            if existing is None:
                alerts[alert_id] = Alert(
                    alert_id=alert_id,
                    rule_number=rule_number,
                    rule_name=str(event.get("rule_name", "")),
                    severity=str(event.get("severity", "")),
                    path=path,
                    first_seen=detected,
                    last_seen=detected,
                    occurrence_count=1,
                    latest_previous=event.get("previous"),
                    latest_current=event.get("current"),
                    status=AlertStatus.UNACKED,
                )
                transitions.append(event)
            else:
                existing.occurrence_count += 1
                existing.last_seen = detected
                existing.latest_previous = event.get("previous")
                existing.latest_current = event.get("current")
                # Re-open acked alerts when fresh drift happens — the user said
                # "I've seen this", and now it's drifted again, so it warrants a
                # new look.
                if existing.status == AlertStatus.ACKED:
                    existing.status = AlertStatus.UNACKED
                    existing.acked_at = None
                    existing.acked_by = None
                    transitions.append(event)
                # else: drift on an already-unacked alert; not a transition.

        _write_alerts_dict(environment, alerts)
    return transitions


def list_alerts(
    environment: str,
    *,
    severity: str | None = None,
    only_unacked: bool = False,
) -> list[Alert]:
    """Return alerts newest-first, with optional severity / status filters."""
    alerts = list(_read_alerts_dict(environment).values())
    alerts.sort(key=lambda a: a.last_seen, reverse=True)
    if severity:
        sev = severity.lower()
        alerts = [a for a in alerts if (a.severity or "").lower() == sev]
    if only_unacked:
        alerts = [a for a in alerts if a.status == AlertStatus.UNACKED]
    return alerts


def ack_alert(environment: str, alert_id: str, *, actor: str = "cli") -> bool:
    """Acknowledge a single alert by id. Returns True if it existed and was unacked."""
    with storage.alerts_lock(environment):
        alerts = _read_alerts_dict(environment)
        alert = alerts.get(alert_id)
        if alert is None or alert.status == AlertStatus.ACKED:
            return False
        alert.status = AlertStatus.ACKED
        alert.acked_at = storage.now_iso()
        alert.acked_by = actor
        _write_alerts_dict(environment, alerts)
    return True


def ack_all(environment: str, *, actor: str = "cli") -> int:
    """Acknowledge every unacked alert. Returns the number flipped."""
    with storage.alerts_lock(environment):
        alerts = _read_alerts_dict(environment)
        flipped = 0
        now = storage.now_iso()
        for alert in alerts.values():
            if alert.status == AlertStatus.UNACKED:
                alert.status = AlertStatus.ACKED
                alert.acked_at = now
                alert.acked_by = actor
                flipped += 1
        if flipped:
            _write_alerts_dict(environment, alerts)
    return flipped


def counts(environment: str) -> dict[str, int]:
    """Cheap unacked count for the topbar pill / CLI banner."""
    return _counts(_read_alerts_dict(environment).values())
