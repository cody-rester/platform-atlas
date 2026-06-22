"""Registry of Platform artifact types and their (probe-verified) API contracts.

Endpoints were verified live against Itential Platform 6 (2026-06-08). The
notable per-app quirks are baked into the table below so the reader/exporter
stay dumb:

- **Workflows** — the list returns full workflow docs; the browse index is
  slimmed with ``include=name,last_updated``. Server-side filtering on the
  automation-studio workflows endpoint (both ``contains`` AND ``equals``) is
  **silently ignored** on this Platform — it always returns the first page — so
  browse uses client-side search over a cached light index
  (``search_mode="client"``) and **export goes by NAME through the legacy
  ``POST /workflow_builder/export`` with body ``{"options": {"name": <name>}}``**
  (an id-based fetch can't be trusted here).
- **Transformations (JST)** — list envelope is ``results`` and server-side
  ``contains`` filtering works, so search is server-side. Export is the full doc
  from ``GET /transformations/{id}``.
- **JSON Forms** — the real path is ``/json-forms/forms`` (a *bare array*, items
  keyed by ``id`` not ``_id``); export is ``GET /json-forms/forms/{id}``.
- **Projects** — list envelope is ``data``; export is
  ``GET /automation-studio/projects/{id}/export`` (inlines every child component
  into one JSON bundle). Projects are few, so search is client-side for safety.

``search_mode``:
  - ``"client"`` — fetch a cached light index for the type and filter/paginate
    in-process (robust regardless of whether the endpoint honours ``contains``).
  - ``"server"`` — pass ``contains``/``skip``/``limit`` straight through.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetType:
    """Static description of one exportable Platform artifact type."""

    key: str                          # stable id used in URLs / selection payloads
    label: str                        # human label for the UI
    list_path: str                    # REST path for the list endpoint
    envelope: str                     # response key holding the array; "" = bare array
    paginated: bool                   # endpoint honours server-side skip/limit
    search_mode: str                  # "client" | "server"
    id_field: str                     # primary id field on a list item
    name_field: str                   # display-name field on a list item
    updated_field: str | None         # last-updated field on a list item (if any)
    search_param: str | None          # server-side substring search param (server mode)
    search_field_param: str | None    # param that scopes the search to name_field
    light_param: dict | None          # extra params to slim the browse index
    export_kind: str                  # "refetch_by_id" | "get_by_id" | "project_export"
    export_path: str | None           # "{id}" template for get_by_id / project_export
    bundle_dir: str                   # folder under exports/ in the bundle ZIP


WORKFLOWS = AssetType(
    key="workflows",
    label="Workflows",
    list_path="/automation-studio/workflows",
    envelope="items",
    paginated=True,
    search_mode="client",             # server-side contains is ignored on P6
    id_field="_id",
    name_field="name",
    updated_field="last_updated",
    search_param="contains",
    search_field_param="containsField",
    light_param={"include": "name,last_updated"},
    export_kind="workflow_builder_export",
    export_path="/workflow_builder/export",
    bundle_dir="workflows",
)

TRANSFORMATIONS = AssetType(
    key="transformations",
    label="Transformations (JST)",
    list_path="/transformations",
    envelope="results",
    paginated=True,
    search_mode="server",             # contains filtering confirmed working
    id_field="_id",
    name_field="name",
    updated_field="lastUpdated",
    search_param="contains",
    search_field_param=None,
    light_param=None,
    export_kind="get_by_id",
    export_path="/transformations/{id}",
    bundle_dir="transformations",
)

FORMS = AssetType(
    key="forms",
    label="JSON Forms",
    list_path="/json-forms/forms",
    envelope="",                       # bare JSON array
    paginated=False,                   # endpoint ignores skip/limit (only ~dozens)
    search_mode="client",
    id_field="id",                     # forms use `id`, not `_id`
    name_field="name",
    updated_field="lastUpdated",
    search_param=None,
    search_field_param=None,
    light_param=None,
    export_kind="get_by_id",
    export_path="/json-forms/forms/{id}",
    bundle_dir="json-forms",
)

PROJECTS = AssetType(
    key="projects",
    label="Projects",
    list_path="/automation-studio/projects",
    envelope="data",
    paginated=True,
    search_mode="client",             # projects are few; client-side is robust
    id_field="_id",
    name_field="name",
    updated_field="lastUpdated",
    search_param="contains",
    search_field_param="containsField",
    light_param=None,
    export_kind="project_export",
    export_path="/automation-studio/projects/{id}/export",
    bundle_dir="projects",
)

ASSET_TYPES: dict[str, AssetType] = {
    a.key: a for a in (WORKFLOWS, TRANSFORMATIONS, FORMS, PROJECTS)
}
