from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from scripts.visuals.spatial_heatmap_dashboard import build_spatial_bins as bins
from scripts.visuals.spatial_heatmap_dashboard import dashboard
from scripts.visuals.spatial_heatmap_dashboard import query


def write_dashboard_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "uuid": ["1", "2", "3", "4", "5"],
            "family": ["A", "A", "B", "C", "C"],
            "species": ["Alpha one", "Alpha one", "Beta one", "Gamma one", None],
            "scientificName": ["Alpha one", "Alpha one", "Beta one", "Gamma one", "Unknown"],
            "taxonConceptID": ["t1", "t1", "t2", "t3", None],
            "stateProvince": ["Victoria", "New South Wales", "Queensland", "Victoria", "Victoria"],
            "year": [2010, 2011, 2012, 2013, 2014],
            "decimalLatitude": [-37.81, -33.86, -27.47, None, -37.20],
            "decimalLongitude": [144.96, 151.21, 153.03, 145.0, None],
        }
    ).write_parquet(path)


def test_build_grid_bins_excludes_null_coordinates_and_aggregates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "butterflies_cleaned.parquet"
    out_dir = tmp_path / "dashboard"
    write_dashboard_fixture(source)

    outputs = bins.build_spatial_bins(
        source_path=source,
        output_dir=out_dir,
        grid_decimals=1,
        h3_resolution=None,
    )

    grid = pl.read_parquet(outputs.grid_bins)
    dimensions = json.loads(outputs.dimensions_json.read_text(encoding="utf-8"))

    assert grid.height == 3
    assert set(grid.columns) >= {
        "lat_bin",
        "lon_bin",
        "family",
        "species",
        "year",
        "stateProvince",
        "record_count",
        "distinct_scientific_names",
        "distinct_taxon_concepts",
    }
    assert grid.filter(pl.col("family") == "A")["record_count"].sum() == 2
    assert dimensions["row_count"] == 5
    assert dimensions["mapped_row_count"] == 3
    assert dimensions["species_values"] == ["Alpha one", "Beta one", "Gamma one"]


def test_query_grid_bins_applies_include_exclude_and_year_range(
    tmp_path: Path,
) -> None:
    source = tmp_path / "butterflies_cleaned.parquet"
    out_dir = tmp_path / "dashboard"
    write_dashboard_fixture(source)
    outputs = bins.build_spatial_bins(
        source_path=source,
        output_dir=out_dir,
        grid_decimals=1,
        h3_resolution=None,
    )
    filters = query.SlicerState(
        include_families=["A", "B"],
        exclude_species=["Beta one"],
        include_states=["Victoria", "New South Wales", "Queensland"],
        year_min=2010,
        year_max=2011,
    )

    rows = query.query_grid_bins(outputs.grid_bins, filters)

    assert len(rows) == 2
    assert {row["family"] for row in rows} == {"A"}
    assert sum(row["record_count"] for row in rows) == 2


def test_cross_filter_options_respect_current_slicer_state(tmp_path: Path) -> None:
    source = tmp_path / "butterflies_cleaned.parquet"
    out_dir = tmp_path / "dashboard"
    write_dashboard_fixture(source)
    outputs = bins.build_spatial_bins(
        source_path=source,
        output_dir=out_dir,
        grid_decimals=1,
        h3_resolution=None,
    )
    filters = query.SlicerState(include_families=["A"])

    options = query.option_values(outputs.grid_bins, filters)

    assert options["families"] == ["A"]
    assert options["species"] == ["Alpha one"]
    assert options["states"] == ["New South Wales", "Victoria"]
    assert options["years"] == [2010, 2011]


def test_year_summary_supports_year_comparison(tmp_path: Path) -> None:
    source = tmp_path / "butterflies_cleaned.parquet"
    out_dir = tmp_path / "dashboard"
    write_dashboard_fixture(source)
    outputs = bins.build_spatial_bins(
        source_path=source,
        output_dir=out_dir,
        grid_decimals=1,
        h3_resolution=None,
    )
    filters = query.SlicerState(include_families=["A", "B"])

    rows = query.year_summary(outputs.grid_bins, filters)

    assert rows == [
        {"year": 2010, "record_count": 1},
        {"year": 2011, "record_count": 1},
        {"year": 2012, "record_count": 1},
    ]


def test_dashboard_precomputes_deck_visual_fields() -> None:
    rows = dashboard.add_visual_fields(
        [{"species": "Alpha one", "record_count": 4, "lat_bin": -37.8, "lon_bin": 144.9}]
    )

    assert rows[0]["color"] == dashboard.species_color("Alpha one")
    assert rows[0]["radius"] == 700
