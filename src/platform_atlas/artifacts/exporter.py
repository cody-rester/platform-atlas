"""Export selected Platform artifacts to bundle-ready JSON files.

The exported JSON is the **raw Platform document, byte-for-byte** — no redaction
or reshaping — so the artifacts re-import cleanly on the receiving side. Per the
project rule, a single failed artifact is recorded in the manifest but never
aborts the export (partial failure = still success).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from platform_atlas.artifacts.catalog import ASSET_TYPES
from platform_atlas.artifacts.client import get_client

_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass
class ExportedFile:
    """One file destined for the bundle ZIP (path is relative to the ZIP root)."""

    path: str
    data: bytes


@dataclass
class ExportItem:
    """Per-artifact outcome, recorded in EXPORT_MANIFEST.json."""

    type: str
    id: str
    name: str
    path: str | None
    bytes: int
    sha256: str | None
    ok: bool
    error: str | None = None


@dataclass
class ExportResult:
    files: list[ExportedFile]
    manifest: dict
    ok_count: int
    error_count: int


def _sanitize(name: str | None, fallback: str) -> str:
    """Make an artifact name safe to use as a filename."""
    cleaned = _UNSAFE.sub("_", (name or "").strip()).strip(". ")
    return (cleaned or fallback)[:120]


def export_one(asset_key: str, asset_id: str, *, name: str | None = None, client: Any = None) -> Any:
    """Fetch and return the raw export document for a single artifact."""
    asset = ASSET_TYPES[asset_key]
    client = client or get_client()

    if asset.export_kind == "workflow_builder_export":
        # Workflows export by NAME via the legacy workflow_builder app. The
        # automation-studio id filters are ignored on P6 (they return the first
        # workflow regardless), so an id-based fetch yields the wrong document.
        if not name:
            raise RuntimeError(f"workflow export requires a name (id={asset_id!r})")
        return client.post(asset.export_path, json={"options": {"name": name}}).json()

    # get_by_id and project_export both resolve to a single GET on a templated path.
    return client.get(asset.export_path.format(id=asset_id)).json()


def export_selection(selection: dict[str, list], *, on_progress=None, client: Any = None) -> ExportResult:
    """Export every artifact in ``selection`` and return bundle files + manifest.

    ``selection`` maps an asset-type key to a list of entries; each entry is
    either an id string or a ``{"id", "name"}`` dict. ``on_progress`` (if given)
    is called as ``(type_label, name, done, total)`` per artifact.
    """
    client = client or get_client()
    files: list[ExportedFile] = []
    results: list[ExportItem] = []
    used_names: dict[str, set[str]] = {}
    total = sum(len(v) for v in selection.values())
    done = 0

    for type_key, entries in selection.items():
        asset = ASSET_TYPES.get(type_key)
        if asset is None:
            continue
        for entry in entries:
            if isinstance(entry, dict):
                aid = str(entry.get("id") or entry.get("_id") or "")
                aname = entry.get("name")
            else:
                aid, aname = str(entry), None
            done += 1
            if on_progress:
                on_progress(asset.label, aname or aid, done, total)
            if not aid:
                results.append(ExportItem(type_key, "", aname or "", None, 0, None, False, "missing id"))
                continue
            try:
                doc = export_one(type_key, aid, name=aname, client=client)
                disp = aname or (doc.get("name") if isinstance(doc, dict) else None) or aid
                base = _sanitize(str(disp), aid)
                seen = used_names.setdefault(asset.bundle_dir, set())
                fname, n = base, 2
                while fname in seen:
                    fname = f"{base}-{n}"
                    n += 1
                seen.add(fname)
                path = f"exports/{asset.bundle_dir}/{fname}.json"
                data = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
                files.append(ExportedFile(path=path, data=data))
                results.append(ExportItem(type_key, aid, str(disp), path, len(data),
                                          hashlib.sha256(data).hexdigest(), True))
            except Exception as exc:  # noqa: BLE001 — partial failure is non-fatal
                results.append(ExportItem(type_key, aid, str(aname or aid), None, 0, None,
                                          False, f"{type(exc).__name__}: {str(exc)[:200]}"))

    ok = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok)
    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "ok": ok,
        "failed": failed,
        "items": [r.__dict__ for r in results],
    }
    files.append(ExportedFile(
        path="exports/EXPORT_MANIFEST.json",
        data=json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
    ))
    return ExportResult(files=files, manifest=manifest, ok_count=ok, error_count=failed)
