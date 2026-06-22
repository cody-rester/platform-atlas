"""Platform artifact export engine.

Read and export Platform assets — Workflows, JST Transformations, JSON Forms and
whole Projects — over the Platform OAuth API, for inclusion in a support bundle.

Pure library, no UI: the WebUI calls :func:`list_assets` to populate the picker
and :func:`export_selection` to produce bundle-ready files. Works in either tier
(the ``platform`` module is Standard-tier safe). Per Cody's decision, exported
JSON is the raw Platform document — no redaction.
"""
from platform_atlas.artifacts.catalog import ASSET_TYPES, AssetType
from platform_atlas.artifacts.client import get_client, reset_client_cache
from platform_atlas.artifacts.exporter import (
    ExportedFile,
    ExportItem,
    ExportResult,
    export_one,
    export_selection,
)
from platform_atlas.artifacts.reader import (
    AssetPage,
    AssetSummary,
    list_assets,
    page_to_dict,
    reset_index_cache,
)

__all__ = [
    "ASSET_TYPES",
    "AssetType",
    "AssetPage",
    "AssetSummary",
    "list_assets",
    "page_to_dict",
    "reset_index_cache",
    "ExportedFile",
    "ExportItem",
    "ExportResult",
    "export_one",
    "export_selection",
    "get_client",
    "reset_client_cache",
]
