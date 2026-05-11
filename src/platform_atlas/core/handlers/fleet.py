# pylint: disable=line-too-long
"""
Dispatch Handler ::: Fleet

Subcommands:
    fleet status   — multi-environment compliance overview from local cache.

Read-only — never triggers a capture or network I/O.
"""

from __future__ import annotations

import json
import logging
from argparse import Namespace

from rich.console import Console
from rich.table import Table

from platform_atlas.core import ui
from platform_atlas.core.fleet import collect_fleet
from platform_atlas.core.registry import registry

logger = logging.getLogger(__name__)
theme = ui.theme
console = Console()


_SEVERITY_STYLE: dict[str, str] = {
    "critical": "bold red",
    "high":     "red",
    "warning":  "yellow",
    "medium":   "yellow",
    "info":     theme.text_dim,
    "low":      theme.text_dim,
}


def _format_age(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _format_pass_rate(pct: float | None) -> str:
    if pct is None:
        return "—"
    if pct >= 95:
        style = theme.success
    elif pct >= 80:
        style = theme.warning
    else:
        style = theme.error
    return f"[{style}]{pct:.1f}%[/{style}]"


def _format_continuous(entry) -> str:
    if not entry.continuous_enabled:
        return f"[{theme.text_dim}]off[/{theme.text_dim}]"
    age = _format_age(entry.continuous_last_run_age_seconds)
    last_status = (entry.continuous_last_status or "").lower()
    if last_status == "ok":
        bullet = f"[{theme.success}]●[/{theme.success}]"
    elif last_status == "error":
        bullet = f"[{theme.error}]●[/{theme.error}]"
    else:
        bullet = f"[{theme.text_dim}]●[/{theme.text_dim}]"
    suffix = ""
    if entry.continuous_previous_unreadable:
        suffix = f" [{theme.warning}](drift skipped)[/{theme.warning}]"
    return f"{bullet} on · {age}{suffix}"


def _format_alerts(entry) -> str:
    if entry.alerts_total == 0:
        return f"[{theme.text_dim}]0[/{theme.text_dim}]"
    sev = (entry.alerts_worst_unacked_severity or "").lower()
    style = _SEVERITY_STYLE.get(sev, theme.text_dim)
    return f"[{style}]{entry.alerts_unacked}[/{style}] / {entry.alerts_total}"


@registry.register("fleet", "status",
                   description="Multi-environment compliance overview from local cache")
def handle_fleet_status(args: Namespace) -> int:
    entries, summary = collect_fleet()

    if getattr(args, "as_json", False):
        payload = {
            "summary": summary.to_dict(),
            "environments": [e.to_dict() for e in entries],
        }
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return 0

    if not entries:
        console.print(
            f"[{theme.text_dim}]No environments configured. Create one with "
            f"[bold]platform-atlas env new[/bold].[/{theme.text_dim}]"
        )
        return 0

    # Header summary
    pass_rate_str = _format_pass_rate(summary.fleet_pass_rate_pct)
    worst_sev = summary.worst_unacked_severity or "—"
    sev_style = _SEVERITY_STYLE.get(worst_sev, theme.text_dim) if worst_sev != "—" else theme.text_dim
    worst_sev_str = f"[{sev_style}]{worst_sev}[/{sev_style}]"
    console.print(
        f"\n[bold]Fleet · {summary.total_envs} environment{'s' if summary.total_envs != 1 else ''}[/bold]  "
        f"[{theme.text_dim}]│[/{theme.text_dim}]  "
        f"continuous: {summary.continuous_enabled_envs}/{summary.total_envs}  "
        f"[{theme.text_dim}]│[/{theme.text_dim}]  "
        f"unacked: {summary.total_unacked_alerts}  "
        f"[{theme.text_dim}]│[/{theme.text_dim}]  "
        f"fleet pass rate: {pass_rate_str}  "
        f"[{theme.text_dim}]│[/{theme.text_dim}]  "
        f"worst severity: {worst_sev_str}\n"
    )

    table = Table(show_header=True, header_style=f"bold {theme.accent}", row_styles=[""])
    table.add_column("Environment", no_wrap=True)
    table.add_column("Tier", no_wrap=True)
    table.add_column("Last session", no_wrap=True, overflow="fold")
    table.add_column("Age", no_wrap=True, justify="right")
    table.add_column("Pass rate", no_wrap=True, justify="right")
    table.add_column("Continuous", no_wrap=True)
    table.add_column("Alerts U/T", no_wrap=True, justify="right")

    for entry in entries:
        name_cell = f"[bold]{entry.name}[/bold]"
        if entry.is_active:
            name_cell = f"[{theme.accent}]●[/{theme.accent}] {name_cell}"
        else:
            name_cell = f"  {name_cell}"
        tier_cell = entry.tier or f"[{theme.text_dim}](inherit)[/{theme.text_dim}]"
        last_session = entry.last_session_name or f"[{theme.text_dim}]—[/{theme.text_dim}]"
        if entry.last_session_status:
            last_session = f"{last_session} [{theme.text_dim}]· {entry.last_session_status}[/{theme.text_dim}]"
        table.add_row(
            name_cell,
            tier_cell,
            last_session,
            _format_age(entry.last_session_age_seconds),
            _format_pass_rate(entry.pass_rate_pct),
            _format_continuous(entry),
            _format_alerts(entry),
        )

    console.print(table)
    console.print(
        f"\n[{theme.text_dim}]Read-only snapshot · captures are not triggered by this command.[/{theme.text_dim}]\n"
    )
    return 0
