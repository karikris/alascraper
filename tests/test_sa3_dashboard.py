from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from scripts.visuals.spatial_heatmap_dashboard import build_sa3_bins
from scripts.visuals.spatial_heatmap_dashboard import dashboard
from scripts.visuals.spatial_heatmap_dashboard import query


def write_sa3_bins_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "sa3_code_2021": ["20101", "20101", "20101", "30101", "30101"],
            "sa3_name_2021": [
                "Melbourne City",
                "Melbourne City",
                "Melbourne City",
                "Brisbane Inner",
                "Brisbane Inner",
            ],
            "family": ["A", "B", "A", "A", "A"],
            "genus": ["Alpha", "Beta", "Alpha", "Gamma", "Gamma"],
            "species": ["Alpha one", "Beta one", "Alpha two", "Gamma one", "Gamma two"],
            "scientificName": [
                "Alpha one",
                "Beta one",
                "Alpha two",
                "Gamma one",
                "Gamma two subspecies",
            ],
            "stateProvince": ["Victoria", "Victoria", "Victoria", "Queensland", "Queensland"],
            "year": [2010, 2010, 2011, 2020, 2021],
            "record_count": [7, 3, 1, 5, 2],
            "distinct_scientific_names": [1, 1, 1, 1, 1],
            "distinct_taxon_concepts": [1, 1, 1, 1, 1],
            "min_year": [2010, 2010, 2011, 2020, 2021],
            "max_year": [2010, 2010, 2011, 2020, 2021],
            "Status": [None, None, "Endangered", None, None],
            "state_status": [None, None, "VIC: Endangered", None, None],
            "state_status_level": [None, None, "Endangered", None, None],
            "state_status_for_occurrence": [None, None, "VIC: Endangered", None, None],
            "state_status_jurisdiction_matched": [None, None, "VIC", None, None],
            "state_status_qualifier": [None, None, None, None, None],
            "epbc_listed_taxon": [None, None, "Alpha two", None, None],
            "state_listed_taxon": [None, None, "Alpha two", None, None],
            "epbc_sprat_url": [None, None, "https://example.test/sprat", None, None],
            "epbc_conservation_advice_url": [None, None, None, None, None],
            "epbc_recovery_plan_url": [None, None, None, None, None],
            "epbc_protected_matters_url": [None, None, None, None, None],
        }
    ).write_parquet(path)


def write_sa3_boundaries_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "sa3_code_2021": ["20101", "30101"],
            "sa3_name_2021": ["Melbourne City", "Brisbane Inner"],
            "state_code_2021": ["2", "3"],
            "state_name_2021": ["Victoria", "Queensland"],
            "area_albers_sqkm": [40.2, 55.6],
            "geometry_wkb": [b"melbourne", b"brisbane"],
            "geometry_geojson": [
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [144.8, -37.9],
                                [145.1, -37.9],
                                [145.1, -37.7],
                                [144.8, -37.7],
                                [144.8, -37.9],
                            ]
                        ],
                    }
                ),
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [152.9, -27.6],
                                [153.2, -27.6],
                                [153.2, -27.3],
                                [152.9, -27.3],
                                [152.9, -27.6],
                            ]
                        ],
                    }
                ),
            ],
        }
    ).write_parquet(path)


def test_sa3_build_defaults_use_abs_gda2020_shapefile_and_since_1950_cutoff() -> None:
    assert build_sa3_bins.SINCE_YEAR_MIN == 1950
    assert build_sa3_bins.DEFAULT_BOUNDARY_DIR == Path(
        "data/boundaries/asgs_ed3/sa3_2021_gda2020"
    )
    assert build_sa3_bins.ABS_SA3_GDA2020_SHAPEFILE_URL.endswith(
        "/SA3_2021_AUST_SHP_GDA2020.zip"
    )


def test_query_sa3_composition_shapes_returns_one_row_per_sa3_with_dominant_family(
    tmp_path: Path,
) -> None:
    bins_path = tmp_path / "butterfly_sa3_bins.parquet"
    boundaries_path = tmp_path / "sa3_boundaries_2021.parquet"
    write_sa3_bins_fixture(bins_path)
    write_sa3_boundaries_fixture(boundaries_path)

    rows = query.query_sa3_composition_shapes(
        bins_path,
        boundaries_path,
        query.SlicerState(include_states=["Victoria"]),
    )

    assert rows == [
        {
            "sa3_code_2021": "20101",
            "sa3_name_2021": "Melbourne City",
            "geometry_geojson": json.loads(
                pl.read_parquet(boundaries_path)
                .filter(pl.col("sa3_code_2021") == "20101")
                .item(0, "geometry_geojson")
            ),
            "total_record_count": 11,
            "color_level": "family",
            "composition": [
                {"value": "A", "record_count": 8, "share": 8 / 11},
                {"value": "B", "record_count": 3, "share": 3 / 11},
            ],
            "composition_text": "A: 8 (72.7%)\nB: 3 (27.3%)",
            "dominant_value": "A",
            "dominant_record_count": 8,
            "dominant_share": 8 / 11,
        }
    ]


def test_query_sa3_composition_shapes_drills_color_level_and_conservation_filter(
    tmp_path: Path,
) -> None:
    bins_path = tmp_path / "butterfly_sa3_bins.parquet"
    boundaries_path = tmp_path / "sa3_boundaries_2021.parquet"
    write_sa3_bins_fixture(bins_path)
    write_sa3_boundaries_fixture(boundaries_path)

    genus_rows = query.query_sa3_composition_shapes(
        bins_path,
        boundaries_path,
        query.SlicerState(include_families=["A"], include_states=["Victoria"]),
    )
    endangered_rows = query.query_sa3_composition_shapes(
        bins_path,
        boundaries_path,
        query.SlicerState(
            conservation_scope="national",
            include_conservation_statuses=["Endangered"],
        ),
        locked_color_dimension="family",
    )

    assert genus_rows[0]["color_level"] == "genus"
    assert genus_rows[0]["composition"] == [
        {"value": "Alpha", "record_count": 8, "share": 1.0}
    ]
    assert endangered_rows[0]["color_level"] == "species"
    assert endangered_rows[0]["dominant_value"] == "Alpha two"
    assert endangered_rows[0]["total_record_count"] == 1


def test_dashboard_precomputes_sa3_polygon_features_with_record_count_opacity() -> None:
    rows = [
        {
            "sa3_code_2021": "20101",
            "sa3_name_2021": "Melbourne City",
            "geometry_geojson": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [144.8, -37.9],
                        [145.1, -37.9],
                        [145.1, -37.7],
                        [144.8, -37.7],
                        [144.8, -37.9],
                    ]
                ],
            },
            "total_record_count": 10,
            "color_level": "family",
            "composition": [
                {"value": "Nymphalidae", "record_count": 7, "share": 0.7},
                {"value": "Lycaenidae", "record_count": 3, "share": 0.3},
            ],
            "composition_text": "Nymphalidae: 7 (70.0%)\nLycaenidae: 3 (30.0%)",
            "dominant_value": "Nymphalidae",
            "dominant_record_count": 7,
            "dominant_share": 0.7,
        },
        {
            "sa3_code_2021": "30101",
            "sa3_name_2021": "Brisbane Inner",
            "geometry_geojson": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [152.9, -27.6],
                        [153.2, -27.6],
                        [153.2, -27.3],
                        [152.9, -27.3],
                        [152.9, -27.6],
                    ]
                ],
            },
            "total_record_count": 100,
            "color_level": "family",
            "composition": [
                {"value": "Nymphalidae", "record_count": 100, "share": 1.0},
            ],
            "composition_text": "Nymphalidae: 100 (100.0%)",
            "dominant_value": "Nymphalidae",
            "dominant_record_count": 100,
            "dominant_share": 1.0,
        },
    ]

    visual_rows = dashboard.add_sa3_polygon_visual_fields(rows)
    features = dashboard.sa3_rows_to_geojson_features(visual_rows)

    assert visual_rows[0]["fill_color"][:3] == dashboard.FAMILY_COLORS["Nymphalidae"][:3]
    assert visual_rows[0]["fill_color"][3] < visual_rows[1]["fill_color"][3]
    assert visual_rows[0]["tooltip_html"].startswith(
        '<div style="font-family:Inter,Arial,sans-serif;line-height:1.35;">'
    )
    assert features["type"] == "FeatureCollection"
    assert len(features["features"]) == 2
    assert features["features"][0]["geometry"]["type"] == "Polygon"
    assert features["features"][0]["properties"]["sa3_name_2021"] == "Melbourne City"
    assert features["features"][0]["properties"]["fill_color"] == visual_rows[0]["fill_color"]
