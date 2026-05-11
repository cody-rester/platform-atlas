"""
Drift detection — diff this run's observed values against the previous run's.

A rule is "drifted" when its ``actual`` value differs from the value the same
rule reported in the immediately prior run. Status changes (PASS→FAIL etc.)
are also considered drift, even when ``actual`` is the same — that surfaces
ruleset edits that flipped a verdict on unchanged config.

Skipped rules are excluded — there's no value to diff against.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _index_previous(previous_run: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Build rule_number → result lookup from the previous run JSON."""
    if not previous_run:
        return {}
    results = previous_run.get("results") or []
    return {
        str(r.get("rule_number", "")): r
        for r in results
        if r.get("rule_number")
    }


def _values_equal(a: Any, b: Any) -> bool:
    """Equality with a few sane normalizations: lists are unordered, dicts are deep.

    Drift comparisons must not flap on benign noise — list reordering, str/int
    coercion of numeric values, and dict-item ordering all count as no-change.
    Lists containing unhashable items (dicts, nested lists) fall back to a
    quadratic match-each-against-each pass instead of erroring out. Container
    structure is walked explicitly rather than via Python's ``==`` so that
    ``True != 1`` semantics hold at every nesting level (Python's dict / list
    equality otherwise treats them as equal because ``bool`` subclasses ``int``).
    """
    # Bool is a subclass of int — guard so True ↔ 1 is treated as drift.
    if isinstance(a, bool) != isinstance(b, bool):
        return False

    # Containers: recurse explicitly so the bool guard above applies at every
    # level. Mixing a dict with a non-dict (or list with non-list) is drift.
    if isinstance(a, dict) or isinstance(b, dict):
        if not (isinstance(a, dict) and isinstance(b, dict)):
            return False
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_values_equal(a[k], b[k]) for k in a)

    if isinstance(a, list) or isinstance(b, list):
        if not (isinstance(a, list) and isinstance(b, list)):
            return False
        if len(a) != len(b):
            return False
        # Greedy match-each-against-each (O(n²)). Drift lists are typically
        # short — adapter lists, index lists, etc. — so the simpler algorithm
        # is fine and correctly handles unhashable items + cross-type coercion.
        remaining = list(b)
        for item in a:
            for j, candidate in enumerate(remaining):
                if _values_equal(item, candidate):
                    remaining.pop(j)
                    break
            else:
                return False
        return not remaining

    # Scalars
    if a == b:
        return True

    if isinstance(a, (int, float)) and isinstance(b, str):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    if isinstance(b, (int, float)) and isinstance(a, str):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False

    return False


def compute_drift(
    *,
    current_records: list[dict[str, Any]],
    previous_run: dict[str, Any] | None,
    current_run_id: str,
    detected_at: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(drift_by_rule, events)`` for the current run.

    - ``drift_by_rule`` maps rule_number → ``{"changed": True, "previous": ..., "since_run_id": ...}``
      — gets merged onto each result in the run report.
    - ``events`` is a list of standalone drift events to append to events.ndjson
      (one per drifted rule).
    """
    if not previous_run:
        # No previous run — first run after enable; no drift signal possible yet.
        return {}, []

    prev_by_rule = _index_previous(previous_run)
    prev_run_id = str(previous_run.get("run_id", ""))

    drift_by_rule: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []

    for rec in current_records:
        rule_number = str(rec.get("rule_number", ""))
        if not rule_number:
            continue
        # SKIP rules don't have a meaningful "actual" — exclude from drift.
        if str(rec.get("status", "")).upper() == "SKIP":
            continue

        prev = prev_by_rule.get(rule_number)
        if prev is None:
            # Rule didn't exist in the previous run — likely a ruleset addition.
            # Don't treat as drift; just skip.
            continue
        if str(prev.get("status", "")).upper() == "SKIP":
            continue

        prev_actual = prev.get("actual")
        cur_actual = rec.get("actual")
        prev_status = str(prev.get("status", "")).upper()
        cur_status = str(rec.get("status", "")).upper()

        value_changed = not _values_equal(prev_actual, cur_actual)
        status_changed = prev_status != cur_status

        if not value_changed and not status_changed:
            continue

        drift_by_rule[rule_number] = {
            "changed": True,
            "previous": prev_actual,
            "current": cur_actual,
            "previous_status": prev_status,
            "current_status": cur_status,
            "since_run_id": prev_run_id,
        }

        events.append({
            "rule_number": rule_number,
            "rule_name": rec.get("name", ""),
            "severity": rec.get("severity", ""),
            "path": rec.get("path", ""),
            "previous": prev_actual,
            "current": cur_actual,
            "previous_status": prev_status,
            "current_status": cur_status,
            "previous_run_id": prev_run_id,
            "current_run_id": current_run_id,
            "detected_at": detected_at,
        })

    if events:
        logger.info("Continuous audit drift: %d rules changed since %s", len(events), prev_run_id)
    return drift_by_rule, events
