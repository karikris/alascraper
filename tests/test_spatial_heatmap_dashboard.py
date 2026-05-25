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


def write_share_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "lat_bin": [-37.8, -37.8, -37.8, -33.9],
            "lon_bin": [145.0, 145.0, 145.0, 151.2],
            "family": ["A", "B", "A", "A"],
            "genus": ["Alpha", "Beta", "Alpha", "Alpha"],
            "species": ["Alpha one", "Beta one", "Alpha two", "Alpha one"],
            "scientificName": ["Alpha one", "Beta one", "Alpha two", "Alpha one"],
            "stateProvince": ["Victoria", "Victoria", "Victoria", "New South Wales"],
            "year": [2010, 2010, 2011, 2011],
            "record_count": [6, 3, 1, 5],
            "distinct_scientific_names": [1, 1, 1, 1],
            "distinct_taxon_concepts": [1, 1, 1, 1],
            "min_year": [2010, 2010, 2011, 2011],
            "max_year": [2010, 2010, 2011, 2011],
        }
    ).write_parquet(path)


def write_many_category_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = [f"Genus {index}" for index in range(10)]
    pl.DataFrame(
        {
            "lat_bin": [-37.8] * 10,
            "lon_bin": [145.0] * 10,
            "family": ["A"] * 10,
            "genus": values,
            "species": [f"{value} species" for value in values],
            "scientificName": [f"{value} species" for value in values],
            "stateProvince": ["Victoria"] * 10,
            "year": [2010] * 10,
            "record_count": list(range(10, 0, -1)),
            "distinct_scientific_names": [1] * 10,
            "distinct_taxon_concepts": [1] * 10,
            "min_year": [2010] * 10,
            "max_year": [2010] * 10,
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


def test_color_value_options_returns_active_color_values_by_count(tmp_path: Path) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)

    options = query.color_value_options(grid, query.SlicerState())

    assert options == [
        {"value": "A", "record_count": 12},
        {"value": "B", "record_count": 3},
    ]


def test_share_heatmap_counts_focus_share_and_top_composition(tmp_path: Path) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)
    filters = query.SlicerState(include_states=["Victoria"])

    rows = query.query_share_heatmap_bins(
        grid,
        filters,
        focus_value="A",
        locked_color_dimension="family",
    )

    assert rows == [
        {
            "lat_bin": -37.8,
            "lon_bin": 145.0,
            "family": "A",
            "genus": None,
            "species": None,
            "scientificName": None,
            "stateProvince": "Victoria",
            "record_count": 7,
            "total_cell_records": 10,
            "share": 0.7,
            "color_level": "family",
            "color_value": "A",
            "composition_text": "A: 7\nB: 3",
        }
    ]


def test_share_heatmap_state_and_year_filters_do_not_change_color_level(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)
    filters = query.SlicerState(
        include_families=["A"],
        include_states=["Victoria"],
        year_min=2011,
        year_max=2011,
    )

    rows = query.query_share_heatmap_bins(grid, filters, focus_value="Alpha")

    assert rows[0]["color_level"] == "genus"
    assert rows[0]["record_count"] == 1
    assert rows[0]["total_cell_records"] == 1
    assert rows[0]["share"] == 1.0


def test_all_share_heatmaps_return_active_family_categories(tmp_path: Path) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)

    rows = query.query_all_share_heatmap_bins(
        grid,
        query.SlicerState(include_states=["Victoria"]),
        locked_color_dimension="family",
    )

    assert [(row["color_value"], row["record_count"]) for row in rows] == [
        ("A", 7),
        ("B", 3),
    ]
    assert {row["color_level"] for row in rows} == {"family"}
    assert {row["total_cell_records"] for row in rows} == {10}
    assert {row["share_percent"] for row in rows} == {"70.0%", "30.0%"}
    assert rows[0]["category_total_records"] == 7
    assert rows[0]["share"] == 0.7


def test_all_share_heatmaps_drill_down_to_genus_species_and_scientific_name(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)

    genus_rows = query.query_all_share_heatmap_bins(
        grid,
        query.SlicerState(include_families=["A"]),
    )
    species_rows = query.query_all_share_heatmap_bins(
        grid,
        query.SlicerState(include_families=["A"], include_genera=["Alpha"]),
    )
    scientific_name_rows = query.query_all_share_heatmap_bins(
        grid,
        query.SlicerState(
            include_families=["A"],
            include_genera=["Alpha"],
            include_species=["Alpha one"],
        ),
    )

    assert {row["color_level"] for row in genus_rows} == {"genus"}
    assert {row["color_value"] for row in genus_rows} == {"Alpha"}
    assert {row["color_level"] for row in species_rows} == {"species"}
    assert {row["color_value"] for row in species_rows} == {"Alpha one", "Alpha two"}
    assert {row["color_level"] for row in scientific_name_rows} == {"scientificName"}
    assert {row["color_value"] for row in scientific_name_rows} == {"Alpha one"}


def test_all_share_heatmaps_state_and_year_filters_do_not_change_color_level(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)
    filters = query.SlicerState(
        include_families=["A"],
        include_states=["Victoria"],
        year_min=2011,
        year_max=2011,
    )

    rows = query.query_all_share_heatmap_bins(grid, filters)

    assert rows == [
        {
            "lat_bin": -37.8,
            "lon_bin": 145.0,
            "record_count": 1,
            "total_cell_records": 1,
            "category_total_records": 1,
            "share": 1.0,
            "share_percent": "100.0%",
            "color_level": "genus",
            "color_value": "Alpha",
            "composition_text": "Alpha: 1 / 1 (100.0%)",
        }
    ]


def test_all_share_heatmaps_cap_categories_and_rows_per_category(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid.parquet"
    write_many_category_fixture(grid)

    rows = query.query_all_share_heatmap_bins(
        grid,
        query.SlicerState(include_families=["A"]),
        max_categories=3,
        limit_per_category=1,
    )

    assert [row["color_value"] for row in rows] == ["Genus 0", "Genus 1", "Genus 2"]
    assert [row["category_total_records"] for row in rows] == [10, 9, 8]
    assert {row["color_level"] for row in rows} == {"genus"}


def test_composition_markers_return_one_row_per_coordinate_with_shares(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)

    rows = query.query_composition_markers(
        grid,
        query.SlicerState(include_states=["Victoria"]),
        locked_color_dimension="family",
    )

    assert rows == [
        {
            "lat_bin": -37.8,
            "lon_bin": 145.0,
            "total_record_count": 10,
            "color_level": "family",
            "composition": [
                {"value": "A", "record_count": 7, "share": 0.7},
                {"value": "B", "record_count": 3, "share": 0.3},
            ],
            "composition_text": "A: 7 (70.0%)\nB: 3 (30.0%)",
        }
    ]


def test_composition_markers_state_and_year_filters_do_not_change_color_level(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid.parquet"
    write_share_fixture(grid)
    filters = query.SlicerState(
        include_families=["A"],
        include_states=["Victoria"],
        year_min=2011,
        year_max=2011,
    )

    rows = query.query_composition_markers(grid, filters)

    assert rows[0]["color_level"] == "genus"
    assert rows[0]["total_record_count"] == 1
    assert rows[0]["composition"] == [
        {"value": "Alpha", "record_count": 1, "share": 1.0}
    ]


def test_composition_markers_collapse_deep_levels_to_top_values_and_other(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid.parquet"
    write_many_category_fixture(grid)

    rows = query.query_composition_markers(
        grid,
        query.SlicerState(include_families=["A"]),
        top_n=8,
    )

    composition = rows[0]["composition"]
    assert rows[0]["color_level"] == "genus"
    assert rows[0]["total_record_count"] == 55
    assert [item["value"] for item in composition] == [
        "Genus 0",
        "Genus 1",
        "Genus 2",
        "Genus 3",
        "Genus 4",
        "Genus 5",
        "Genus 6",
        "Genus 7",
        "Other",
    ]
    assert composition[-1] == {"value": "Other", "record_count": 3, "share": 3 / 55}
    assert round(sum(item["share"] for item in composition), 10) == 1.0


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


def test_dashboard_precomputes_share_heatmap_visual_fields() -> None:
    rows = dashboard.add_share_heatmap_visual_fields(
        [
            {
                "color_level": "family",
                "color_value": "Nymphalidae",
                "record_count": 4,
                "share": 0.25,
            },
            {
                "color_level": "family",
                "color_value": "Nymphalidae",
                "record_count": 4,
                "share": 1.0,
            },
        ]
    )

    assert rows[0]["radius"] == 27_000
    assert rows[0]["color"][:3] == dashboard.FAMILY_COLORS["Nymphalidae"][:3]
    assert rows[0]["color"][3] < rows[1]["color"][3]


def test_dashboard_precomputes_category_share_heatmap_visual_fields() -> None:
    rows = dashboard.add_category_share_heatmap_visual_fields(
        [
            {
                "color_level": "family",
                "color_value": "Nymphalidae",
                "record_count": 2,
                "total_cell_records": 100,
                "share": 0.25,
            },
            {
                "color_level": "family",
                "color_value": "Nymphalidae",
                "record_count": 50,
                "total_cell_records": 100,
                "share": 1.0,
            },
        ]
    )

    assert rows[0]["radius"] == dashboard.point_radius(100)
    assert rows[1]["radius"] == dashboard.point_radius(100)
    assert rows[0]["color"][:3] == dashboard.FAMILY_COLORS["Nymphalidae"][:3]
    assert rows[0]["color"][3] < rows[1]["color"][3]
    assert rows[0]["share_percent"] == "25.0%"


def test_dashboard_dominant_radius_uses_total_record_count() -> None:
    expected_radii = {
        1: 3,
        10: 5,
        25: 6,
        50: 7,
        100: 8,
        250: 9,
        500: 10,
        750: 11,
        1_000: 12,
        2_500: 14,
        5_000: 16,
        7_500: 18,
        10_000: 20,
        15_000: 22,
        20_000: 24,
        25_000: 26,
        50_000: 28,
    }

    for record_count, radius in expected_radii.items():
        assert dashboard.dominant_point_radius(record_count) == radius

    assert dashboard.dominant_point_radius(6_250) == 17
    assert dashboard.dominant_point_radius(100_000) == 28


def test_dashboard_precomputes_dominant_category_visual_fields() -> None:
    rows = dashboard.add_dominant_category_visual_fields(
        [
            {
                "total_record_count": 100,
                "color_level": "family",
                "composition": [
                    {"value": "Nymphalidae", "record_count": 70, "share": 0.7},
                    {"value": "Lycaenidae", "record_count": 30, "share": 0.3},
                ],
                "composition_text": "Nymphalidae: 70 (70.0%)\nLycaenidae: 30 (30.0%)",
            },
            {
                "total_record_count": 100,
                "color_level": "family",
                "composition": [
                    {"value": "Nymphalidae", "record_count": 25, "share": 0.25},
                    {"value": "Lycaenidae", "record_count": 75, "share": 0.75},
                ],
                "composition_text": "Nymphalidae: 25 (25.0%)\nLycaenidae: 75 (75.0%)",
            },
        ]
    )

    high_share = rows[0]
    low_share = rows[1]

    assert high_share["dominant_value"] == "Nymphalidae"
    assert high_share["dominant_record_count"] == 70
    assert high_share["dominant_share"] == 0.7
    assert high_share["dominant_share_percent"] == "70.0%"
    assert high_share["fill_color"][:3] == dashboard.FAMILY_COLORS["Nymphalidae"][:3]
    assert high_share["radius_pixels"] == dashboard.dominant_point_radius(100)
    assert high_share["tooltip"].startswith("Dominant family Nymphalidae: 70.0%")
    assert high_share["fill_color"][3] > low_share["fill_color"][3]


def test_dashboard_builds_piechart_icon_visual_fields() -> None:
    rows = dashboard.add_piechart_visual_fields(
        [
            {
                "total_record_count": 10,
                "color_level": "family",
                "composition": [
                    {"value": "Nymphalidae", "record_count": 7, "share": 0.7},
                    {"value": "Lycaenidae", "record_count": 3, "share": 0.3},
                ],
            }
        ]
    )

    assert rows[0]["icon_size"] > dashboard.PIE_ICON_MIN_SIZE_PX
    assert rows[0]["icon_data"]["url"].startswith("data:image/svg+xml;charset=utf-8,")
    assert rows[0]["icon_data"]["width"] == dashboard.PIE_ICON_CANVAS_PX
    assert rows[0]["icon_data"]["height"] == dashboard.PIE_ICON_CANVAS_PX


def test_dashboard_piechart_size_uses_total_observation_count() -> None:
    assert dashboard.pie_icon_size(1) == dashboard.PIE_ICON_MIN_SIZE_PX
    assert dashboard.pie_icon_size(1_000) > dashboard.pie_icon_size(10)
    assert dashboard.pie_icon_size(1_000_000) == dashboard.PIE_ICON_MAX_SIZE_PX


def test_dashboard_pie_svg_contains_one_path_per_slice() -> None:
    svg = dashboard.build_pie_svg(
        [
            {"value": "Nymphalidae", "record_count": 7, "share": 0.7},
            {"value": "Other", "record_count": 3, "share": 0.3},
        ],
        color_level="family",
    )

    assert svg.count("<path") == 2
    assert dashboard.color_to_hex(dashboard.FAMILY_COLORS["Nymphalidae"]) in svg
    assert dashboard.OTHER_COLOR_HEX in svg


def test_dashboard_map_display_modes_include_piechart_composition() -> None:
    assert dashboard.PIECHART_COMPOSITION_MODE in dashboard.MAP_DISPLAY_MODES


def test_dashboard_defaults_to_dominant_category_mode() -> None:
    assert dashboard.MAP_DISPLAY_MODES[0] == dashboard.DOMINANT_CATEGORY_MODE


def test_dashboard_compare_category_heatmaps_mode_remains_available() -> None:
    assert dashboard.CATEGORY_SHARE_HEATMAPS_MODE == "Compare category heatmaps"
    assert dashboard.CATEGORY_SHARE_HEATMAPS_MODE in dashboard.MAP_DISPLAY_MODES


def test_dashboard_map_display_selector_uses_versioned_state_key() -> None:
    class FakeSidebar:
        def __init__(self) -> None:
            self.key = None
            self.index = None

        def selectbox(self, *_args: object, **kwargs: object) -> str:
            self.key = kwargs["key"]
            self.index = kwargs["index"]
            return dashboard.DOMINANT_CATEGORY_MODE

    sidebar = FakeSidebar()

    selected = dashboard.map_display_selector(sidebar)

    assert selected == dashboard.DOMINANT_CATEGORY_MODE
    assert sidebar.index == 0
    assert sidebar.key == "map_display_mode_v4"


def test_dashboard_title_is_butterfly_dashboard() -> None:
    assert dashboard.PAGE_TITLE == "Butterfly Dashboard"


def test_dashboard_main_map_height_matches_requested_frame() -> None:
    assert dashboard.MAP_HEIGHT_PX == 1_230


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
