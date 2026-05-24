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
            "genus": ["Alpha", "Alpha", "Beta", "Gamma", None],
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
        "genus",
        "species",
        "scientificName",
        "year",
        "stateProvince",
        "record_count",
        "distinct_scientific_names",
        "distinct_taxon_concepts",
    }
    assert grid.filter(pl.col("family") == "A")["record_count"].sum() == 2
    assert dimensions["row_count"] == 5
    assert dimensions["mapped_row_count"] == 3
    assert dimensions["genus_values"] == ["Alpha", "Beta", "Gamma"]
    assert dimensions["species_values"] == ["Alpha one", "Beta one", "Gamma one"]
    assert dimensions["scientific_name_values"] == ["Alpha one", "Beta one", "Gamma one", "Unknown"]


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
        include_genera=["Alpha", "Beta"],
        exclude_species=["Beta one"],
        include_states=["Victoria", "New South Wales", "Queensland"],
        year_min=2010,
        year_max=2011,
    )

    rows = query.query_grid_bins(outputs.grid_bins, filters)

    assert len(rows) == 2
    assert {row["family"] for row in rows} == {"A"}
    assert {row["color_level"] for row in rows} == {"scientificName"}
    assert {row["color_value"] for row in rows} == {"Alpha one"}
    assert sum(row["record_count"] for row in rows) == 2
    assert query.mapped_record_count(outputs.grid_bins, filters) == 2


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
    assert options["genera"] == ["Alpha"]
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
        [
            {
                "color_level": "genus",
                "color_value": "Alpha",
                "species": "Alpha one",
                "record_count": 4,
                "lat_bin": -37.8,
                "lon_bin": 144.9,
            }
        ]
    )

    assert rows[0]["color"] == dashboard.stable_color("Alpha")
    assert rows[0]["radius"] == 27_000


def test_dashboard_uses_fixed_family_palette() -> None:
    rows = dashboard.add_visual_fields(
        [{"color_level": "family", "color_value": "Nymphalidae", "record_count": 1}]
    )

    assert rows[0]["color"] == dashboard.FAMILY_COLORS["Nymphalidae"]


def test_dashboard_full_year_range_does_not_filter_unknown_years() -> None:
    years = [2010, 2011, 2012]

    assert dashboard.active_year_bounds(years, (2010, 2012)) == (None, None)
    assert dashboard.active_year_bounds(years, (2011, 2012)) == (2011, 2012)


def test_query_color_dimension_follows_taxonomy_filter_specificity() -> None:
    assert query.color_dimension(query.SlicerState()) == "family"
    assert query.color_dimension(query.SlicerState(include_families=["A"])) == "genus"
    assert query.color_dimension(query.SlicerState(exclude_families=["A"])) == "genus"
    assert query.color_dimension(query.SlicerState(include_genera=["Alpha"])) == "species"
    assert (
        query.color_dimension(query.SlicerState(include_species=["Alpha one"]))
        == "scientificName"
    )


def test_query_color_dimension_can_be_locked() -> None:
    filters = query.SlicerState(include_species=["Alpha one"])

    assert query.color_dimension(filters, locked_color_dimension="family") == "family"
    assert query.color_dimension(filters, locked_color_dimension="genus") == "genus"
    assert query.color_dimension(filters, locked_color_dimension="species") == "species"
    assert (
        query.color_dimension(filters, locked_color_dimension="scientificName")
        == "scientificName"
    )
    assert query.color_dimension(filters, locked_color_dimension="unknown") == "scientificName"


def test_query_grid_bins_uses_locked_color_dimension(tmp_path: Path) -> None:
    source = tmp_path / "butterflies_cleaned.parquet"
    out_dir = tmp_path / "dashboard"
    write_dashboard_fixture(source)
    outputs = bins.build_spatial_bins(
        source_path=source,
        output_dir=out_dir,
        grid_decimals=1,
        h3_resolution=None,
    )
    filters = query.SlicerState(include_genera=["Alpha"])

    rows = query.query_grid_bins(
        outputs.grid_bins,
        filters,
        locked_color_dimension="genus",
    )

    assert sorted(rows, key=lambda row: row["year_range"]) == [
        {
            "lat_bin": -37.8,
            "lon_bin": 145.0,
            "family": "A",
            "genus": "Alpha",
            "species": None,
            "scientificName": None,
            "stateProvince": "Victoria",
            "record_count": 1,
            "distinct_scientific_names": 1,
            "distinct_taxon_concepts": 1,
            "min_year": 2010,
            "max_year": 2010,
            "year_range": "2010",
            "color_level": "genus",
            "color_value": "Alpha",
        },
        {
            "lat_bin": -33.9,
            "lon_bin": 151.2,
            "family": "A",
            "genus": "Alpha",
            "species": None,
            "scientificName": None,
            "stateProvince": "New South Wales",
            "record_count": 1,
            "distinct_scientific_names": 1,
            "distinct_taxon_concepts": 1,
            "min_year": 2011,
            "max_year": 2011,
            "year_range": "2011",
            "color_level": "genus",
            "color_value": "Alpha",
        },
    ]


def test_state_selector_reads_explicit_session_state() -> None:
    class FakeSidebar:
        def radio(self, *_args: object, **_kwargs: object) -> str:
            return "Include"

        def multiselect(self, *_args: object, **_kwargs: object) -> list[str]:
            return ["Victoria"]

        def selectbox(self, *_args: object, **kwargs: object) -> str:
            callback = kwargs.get("on_change")
            if callback:
                callback()
            return "East coast"

    session_state = {
        "state_preset": "East coast",
        "state_values": ["Victoria", "Unknown"],
    }

    include_states, exclude_states = dashboard.state_selector(
        ["Victoria", "New South Wales", "Queensland"],
        FakeSidebar(),
        session_state,
    )

    assert include_states == ["Victoria"]
    assert exclude_states == []
    assert session_state["state_values"] == ["Victoria", "New South Wales", "Queensland"]
