# pylint: disable=line-too-long
"""
ATLAS // Architecture & Maintenance Report Renderer

Renders the Architecture & Maintenance report (05_arch.html) containing:
  - Additional Validation (non-log extended checks)
  - Architecture Overview (manually collected deployment data)
"""

from __future__ import annotations

import os
import re
import html as html_mod
import logging
from pathlib import Path
from datetime import datetime, timezone

from platform_atlas.reporting.report_renderer import (
    generate_nonlog_extended_html,
    _render_architecture_section,
)
from platform_atlas.core._version import __version__

logger = logging.getLogger(__name__)


def render_arch_report(
    extended_results: list,
    architecture_data: dict,
    template_path: str | Path,
    output_path: str | Path,
    *,
    title: str = "Architecture & Maintenance",
    subtitle: str = "",
    organization_name: str = "Unknown Organization",
    atlas_version: str = __version__,
    tier: str = "extended",
) -> str:
    """Render the Architecture & Maintenance report to a styled HTML file.

    In Standard mode the architecture section collapses to a tier notice
    — the architecture form is Extended-only, so there is nothing to render.
    """
    template_path = Path(template_path)
    template = template_path.read_text(encoding="utf-8")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    tier_normalized = (tier or "extended").strip().lower()
    is_standard = tier_normalized == "standard"

    if is_standard:
        tier_label = "STANDARD"
        tier_color = "#1B93D2"
        extended_html = (
            '<div class="tier-notice">'
            '<p><strong>Architecture &amp; maintenance audit is part of '
            'Extended Mode.</strong></p>'
            '<p>Standard Mode validates Platform configuration via OAuth only. '
            'Run <code>platform-atlas tier upgrade</code> to enable the full '
            'architecture review.</p>'
            '</div>'
        )
        architecture_html = ""
    else:
        tier_label = "EXTENDED"
        tier_color = "#FF6633"
        extended_html = generate_nonlog_extended_html(extended_results or [])
        architecture_html = _render_architecture_section(architecture_data or {})

    safe_title = html_mod.escape(title)
    safe_subtitle = html_mod.escape(subtitle)
    safe_org = html_mod.escape(organization_name)
    safe_version = html_mod.escape(atlas_version)
    safe_timestamp = html_mod.escape(timestamp)
    safe_tier_label = html_mod.escape(tier_label)
    safe_tier_color = html_mod.escape(tier_color)

    replacements = {
        "{{TITLE}}": safe_title,
        "{{SUBTITLE}}": safe_subtitle,
        "{{ORGANIZATION_NAME}}": safe_org,
        "{{TIMESTAMP}}": safe_timestamp,
        "{{ATLAS_VERSION}}": safe_version,
        "{{EXTENDED_SECTION}}": extended_html,
        "{{ARCHITECTURE_SECTION}}": architecture_html,
        "{{TIER_LABEL}}": safe_tier_label,
        "{{TIER_COLOR}}": safe_tier_color,
    }

    pattern = re.compile("|".join(re.escape(k) for k in replacements))
    html = pattern.sub(lambda m: replacements[m.group(0)], template)

    if output_path:
        output_path = Path(output_path)
        output_path.write_text(html, encoding="utf-8")
        os.chmod(output_path, 0o600)

    return html
