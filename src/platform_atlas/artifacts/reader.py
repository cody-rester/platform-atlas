"""List/search Platform artifacts for the selection UI.

All browsing goes through :func:`list_assets`. Two search strategies, chosen per
type in the catalog:

- ``search_mode="server"`` — pass ``contains``/``skip``/``limit`` straight to the
  endpoint (used for transformations, where it works and the collection is large).
- ``search_mode="client"`` — fetch a cached *light index* of the whole type
  (id/name/updated only) and filter + paginate in-process. Used for workflows
  (whose server-side ``contains`` is silently ignored), forms (a bare array) and
  projects (few). The index is cached briefly per environment so search-as-you-type
  doesn't re-fetch on every keystroke.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from platform_atlas.artifacts.catalog import ASSET_TYPES, AssetType
from platform_atlas.artifacts.client import get_client
from platform_atlas.core.context import ctx

logger = logging.getLogger(__name__)


@dataclass
class AssetSummary:
    """Lightweight row for the picker — never carries the full artifact doc."""

    id: str
    name: str
    updated: str | None


@dataclass
class AssetPage:
    """One page of search results."""

    items: list[AssetSummary]
    total: int
    skip: int
    limit: int
    truncated: bool = False  # True when the light index hit its item ceiling


_INDEX_CACHE: dict[str, tuple[float, list[AssetSummary], bool]] = {}
_INDEX_TTL = 60.0  # seconds — a browse session reuses the index; refreshes after
_LIGHT_INDEX_CAP = 20000  # hard ceiling on a client-side light index (see _fetch_all_light)


def _array(asset: AssetType, body: Any) -> list[dict]:
    """Pull the list of item dicts out of whatever envelope the endpoint uses."""
    if asset.envelope == "":
        raw = body if isinstance(body, list) else []
    elif isinstance(body, dict):
        raw = body.get(asset.envelope)
        raw = raw if isinstance(raw, list) else []
    else:
        raw = []
    return [it for it in raw if isinstance(it, dict)]


def _total(body: Any, count: int) -> int:
    """Best-effort total count from the response, falling back to page length."""
    if isinstance(body, dict):
        if isinstance(body.get("total"), int):
            return body["total"]
        meta = body.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("total"), int):
            return meta["total"]
    return count


def _summary(asset: AssetType, item: dict) -> AssetSummary:
    updated = item.get(asset.updated_field) if asset.updated_field else None
    return AssetSummary(
        id=str(item.get(asset.id_field) or item.get("_id") or item.get("id") or ""),
        name=str(item.get(asset.name_field) or item.get("name") or "(unnamed)"),
        updated=updated or item.get("lastUpdated") or item.get("last_updated") or item.get("created"),
    )


def _fetch_all_light(asset: AssetType, client: Any) -> tuple[list[AssetSummary], bool]:
    """Fetch the full lightweight index for a type, paging the endpoint as needed.

    Returns ``(rows, truncated)`` where ``truncated`` is True when the item
    ceiling was hit before the collection was exhausted — so callers can warn
    that items beyond the cap are not listed.
    """
    light = dict(asset.light_param or {})
    if not asset.paginated:
        body = client.get(asset.list_path, params=light or None).json()
        return [_summary(asset, it) for it in _array(asset, body)], False

    rows: list[AssetSummary] = []
    skip, page = 0, 200
    truncated = False
    while True:
        if skip >= _LIGHT_INDEX_CAP:
            truncated = True
            logger.warning(
                "Artifact light index for %r hit the %d-item ceiling; items beyond "
                "this are not listed or selectable.", asset.key, _LIGHT_INDEX_CAP,
            )
            break
        params = dict(light)
        params["skip"] = skip
        params["limit"] = page
        body = client.get(asset.list_path, params=params).json()
        items = _array(asset, body)
        rows.extend(_summary(asset, it) for it in items)
        # Advance by what was actually returned (robust to a server-side limit cap),
        # and stop on the reported total rather than on a short page.
        if not items:
            break
        skip += len(items)
        if skip >= _total(body, len(rows)):
            break
    return rows, truncated


def _light_index(asset: AssetType, client: Any) -> tuple[list[AssetSummary], bool]:
    """Return the cached light index for a type (per active environment)."""
    key = f"{ctx().config.platform_uri}|{asset.key}"
    now = time.monotonic()
    cached = _INDEX_CACHE.get(key)
    if cached and (now - cached[0]) < _INDEX_TTL:
        return cached[1], cached[2]
    rows, truncated = _fetch_all_light(asset, client)
    _INDEX_CACHE[key] = (now, rows, truncated)
    return rows, truncated


def reset_index_cache() -> None:
    """Drop cached light indexes (e.g. after an environment switch)."""
    _INDEX_CACHE.clear()


def list_assets(
    asset_key: str,
    *,
    search: str = "",
    skip: int = 0,
    limit: int = 25,
    client: Any = None,
) -> AssetPage:
    """Return one page of artifacts of ``asset_key`` matching ``search``."""
    asset = ASSET_TYPES[asset_key]
    client = client or get_client()
    skip = max(0, int(skip))
    limit = max(1, int(limit))

    if asset.search_mode == "client":
        rows, truncated = _light_index(asset, client)
        if search:
            needle = search.lower()
            rows = [r for r in rows if needle in r.name.lower()]
        return AssetPage(items=rows[skip:skip + limit], total=len(rows),
                         skip=skip, limit=limit, truncated=truncated)

    # server-side search + pagination
    params: dict[str, Any] = {}
    if asset.light_param:
        params.update(asset.light_param)
    params["skip"] = skip
    params["limit"] = limit
    if search and asset.search_param:
        params[asset.search_param] = search
        if asset.search_field_param:
            params[asset.search_field_param] = asset.name_field
    body = client.get(asset.list_path, params=params).json()
    rows = [_summary(asset, it) for it in _array(asset, body)]
    return AssetPage(items=rows, total=_total(body, len(rows)), skip=skip, limit=limit)


def page_to_dict(page: AssetPage) -> dict:
    """JSON-serializable form for the WebUI API route."""
    return {
        "items": [{"id": r.id, "name": r.name, "updated": r.updated} for r in page.items],
        "total": page.total,
        "skip": page.skip,
        "limit": page.limit,
        "truncated": page.truncated,
    }
