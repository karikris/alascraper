from __future__ import annotations

from typing import Any

from scripts.visuals.spatial_heatmap_dashboard import dashboard


def test_query_dispatch_passes_coordinate_precision_when_supported() -> None:
    seen: dict[str, Any] = {}

    def query_function(
        grid_path: str,
        filters: str,
        *,
        limit: int,
        locked_color_dimension: str | None = None,
        coordinate_decimals: int | None = None,
    ) -> list[dict[str, object]]:
        seen.update(
            {
                "grid_path": grid_path,
                "filters": filters,
                "limit": limit,
                "locked_color_dimension": locked_color_dimension,
                "coordinate_decimals": coordinate_decimals,
            }
        )
        return [{"ok": True}]

    rows = dashboard.query_with_coordinate_precision(
        query_function,
        "grid.parquet",
        "filters",
        coordinate_decimals=1,
        limit=25,
        locked_color_dimension="family",
    )

    assert rows == [{"ok": True}]
    assert seen == {
        "grid_path": "grid.parquet",
        "filters": "filters",
        "limit": 25,
        "locked_color_dimension": "family",
        "coordinate_decimals": 1,
    }


def test_query_dispatch_omits_coordinate_precision_when_unsupported() -> None:
    seen: dict[str, Any] = {}

    def query_function(
        grid_path: str,
        filters: str,
        *,
        limit: int,
        locked_color_dimension: str | None = None,
    ) -> list[dict[str, object]]:
        seen.update(
            {
                "grid_path": grid_path,
                "filters": filters,
                "limit": limit,
                "locked_color_dimension": locked_color_dimension,
            }
        )
        return [{"ok": True}]

    rows = dashboard.query_with_coordinate_precision(
        query_function,
        "grid.parquet",
        "filters",
        coordinate_decimals=1,
        limit=25,
        locked_color_dimension="family",
    )

    assert rows == [{"ok": True}]
    assert seen == {
        "grid_path": "grid.parquet",
        "filters": "filters",
        "limit": 25,
        "locked_color_dimension": "family",
    }
