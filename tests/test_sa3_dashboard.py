from __future__ import annotations

import json
import sys
import types
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


def write_sa2_bins_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "sa2_code_2021": ["201011001", "201011001", "301011001"],
            "sa2_name_2021": ["Carlton", "Carlton", "Brisbane City"],
            "family": ["A", "B", "A"],
            "genus": ["Alpha", "Beta", "Gamma"],
            "species": ["Alpha one", "Beta one", "Gamma one"],
            "scientificName": ["Alpha one", "Beta one", "Gamma one"],
            "stateProvince": ["Victoria", "Victoria", "Queensland"],
            "year": [2010, 2010, 2020],
            "record_count": [11, 4, 9],
            "distinct_scientific_names": [1, 1, 1],
            "distinct_taxon_concepts": [1, 1, 1],
            "min_year": [2010, 2010, 2020],
            "max_year": [2010, 2010, 2020],
            "Status": [None, None, None],
            "state_status": [None, None, None],
            "state_status_level": [None, None, None],
            "state_status_for_occurrence": [None, None, None],
            "state_status_jurisdiction_matched": [None, None, None],
            "state_status_qualifier": [None, None, None],
            "epbc_listed_taxon": [None, None, None],
            "state_listed_taxon": [None, None, None],
            "epbc_sprat_url": [None, None, None],
            "epbc_conservation_advice_url": [None, None, None],
            "epbc_recovery_plan_url": [None, None, None],
            "epbc_protected_matters_url": [None, None, None],
        }
    ).write_parquet(path)


def write_sa2_boundaries_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "sa2_code_2021": ["201011001", "301011001"],
            "sa2_name_2021": ["Carlton", "Brisbane City"],
            "state_code_2021": ["2", "3"],
            "state_name_2021": ["Victoria", "Queensland"],
            "area_albers_sqkm": [3.1, 7.4],
            "geometry_wkb": [b"carlton", b"brisbane-city"],
            "geometry_geojson": [
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [144.95, -37.82],
                                [144.98, -37.82],
                                [144.98, -37.79],
                                [144.95, -37.79],
                                [144.95, -37.82],
                            ]
                        ],
                    }
                ),
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [153.01, -27.48],
                                [153.04, -27.48],
                                [153.04, -27.45],
                                [153.01, -27.45],
                                [153.01, -27.48],
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


def test_abs_area_level_configs_are_ordered_sa1_to_sa3_with_sa2_default() -> None:
    assert build_sa3_bins.DEFAULT_AREA_LEVEL == "SA2"
    assert list(build_sa3_bins.AREA_LEVEL_CONFIGS) == ["SA1", "SA2", "SA3"]

    sa1 = build_sa3_bins.AREA_LEVEL_CONFIGS["SA1"]
    sa2 = build_sa3_bins.AREA_LEVEL_CONFIGS["SA2"]
    assert sa1.boundary_dir == Path("data/boundaries/asgs_ed3/sa1_2021_gda2020")
    assert sa2.boundary_dir == Path("data/boundaries/asgs_ed3/sa2_2021_gda2020")
    assert sa2.download_url.endswith("/SA2_2021_AUST_SHP_GDA2020.zip")
    assert sa2.bins_filename == "butterfly_sa2_bins.parquet"


def test_dashboard_loads_sibling_query_when_plain_query_module_is_stale(
    monkeypatch,
) -> None:
    stale_query = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "query", stale_query)

    loaded_query = dashboard.load_query_module()

    assert loaded_query is not stale_query
    assert hasattr(loaded_query, "query_sa3_composition_shapes")


def test_dashboard_area_level_selector_orders_sa1_to_sa3_and_defaults_to_sa2() -> None:
    class FakeSt:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def radio(self, label: str, options: list[str], **kwargs: object) -> str:
            self.calls.append({"label": label, "options": options, **kwargs})
            return options[int(kwargs["index"])]

    fake_st = FakeSt()

    selected = dashboard.area_level_selector(fake_st)

    assert selected == "SA2"
    assert fake_st.calls == [
        {
            "label": "ABS area level",
            "options": ["SA1", "SA2", "SA3"],
            "index": 1,
            "horizontal": True,
            "key": "area_level_v1",
        }
    ]


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
            "area_level": "SA3",
            "area_code_2021": "20101",
            "area_name_2021": "Melbourne City",
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


def test_query_area_composition_shapes_returns_one_row_per_selected_abs_area(
    tmp_path: Path,
) -> None:
    bins_path = tmp_path / "butterfly_sa2_bins.parquet"
    boundaries_path = tmp_path / "sa2_boundaries_2021.parquet"
    write_sa2_bins_fixture(bins_path)
    write_sa2_boundaries_fixture(boundaries_path)

    rows = query.query_area_composition_shapes(
        bins_path,
        boundaries_path,
        query.SlicerState(include_states=["Victoria"]),
        area_code_column="sa2_code_2021",
        area_name_column="sa2_name_2021",
        area_label="SA2",
    )

    assert rows == [
        {
            "area_level": "SA2",
            "area_code_2021": "201011001",
            "area_name_2021": "Carlton",
            "geometry_geojson": json.loads(
                pl.read_parquet(boundaries_path)
                .filter(pl.col("sa2_code_2021") == "201011001")
                .item(0, "geometry_geojson")
            ),
            "total_record_count": 15,
            "color_level": "family",
            "composition": [
                {"value": "A", "record_count": 11, "share": 11 / 15},
                {"value": "B", "record_count": 4, "share": 4 / 15},
            ],
            "composition_text": "A: 11 (73.3%)\nB: 4 (26.7%)",
            "dominant_value": "A",
            "dominant_record_count": 11,
            "dominant_share": 11 / 15,
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
    assert features["features"][0]["tooltip_html"] == visual_rows[0]["tooltip_html"]
    assert features["features"][0]["properties"]["sa3_name_2021"] == "Melbourne City"
    assert features["features"][0]["properties"]["fill_color"] == visual_rows[0]["fill_color"]
    assert "{properties.tooltip_html}" not in features["features"][0]["tooltip_html"]


def test_dashboard_sa3_tooltip_shows_sorted_composition_table_and_pie() -> None:
    row = {
        "sa3_name_2021": "Melbourne City",
        "total_record_count": 100,
        "color_level": "species",
        "composition": [
            {"value": "Butterfly least", "record_count": 5, "share": 0.05},
            {"value": "Butterfly dominant", "record_count": 70, "share": 0.7},
            {"value": "Butterfly middle", "record_count": 25, "share": 0.25},
        ],
        "composition_text": (
            "Butterfly dominant: 70 (70.0%)\n"
            "Butterfly middle: 25 (25.0%)\n"
            "Butterfly least: 5 (5.0%)"
        ),
        "dominant_value": "Butterfly dominant",
        "dominant_record_count": 70,
        "dominant_share": 0.7,
    }

    tooltip_html = dashboard.build_sa3_tooltip_html(
        row,
        dashboard.pie_svg_data_url(
            dashboard.build_pie_svg(row["composition"], color_level="species")
        ),
    )

    assert '<img src="data:image/svg+xml;charset=utf-8,' in tooltip_html
    assert "Total records" in tooltip_html
    assert "Dominant records" in tooltip_html
    assert "Composition by species" in tooltip_html
    assert tooltip_html.index("Butterfly dominant") < tooltip_html.index("Butterfly middle")
    assert tooltip_html.index("Butterfly middle") < tooltip_html.index("Butterfly least")
    assert "70.0%" in tooltip_html
    assert "25.0%" in tooltip_html
    assert "5.0%" in tooltip_html


def test_dashboard_renders_sa3_dominant_map_with_geojson_layer() -> None:
    class FakePdk:
        class ViewState:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class Layer:
            def __init__(self, layer_name: str, data: object, **kwargs: object) -> None:
                self.layer_name = layer_name
                self.data = data
                self.kwargs = kwargs

        class Deck:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

    class FakeSt:
        def __init__(self) -> None:
            self.deck = None
            self.height = None

        def pydeck_chart(self, deck: object, **kwargs: object) -> None:
            self.deck = deck
            self.height = kwargs["height"]

    st = FakeSt()

    dashboard.render_sa3_dominant_map(
        [
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
            }
        ],
        st,
        FakePdk,
    )

    layer = st.deck.kwargs["layers"][0]
    assert layer.layer_name == "GeoJsonLayer"
    assert layer.kwargs["get_fill_color"] == "properties.fill_color"
    assert layer.kwargs["get_line_color"] == "properties.line_color"
    assert layer.kwargs["pickable"] is True
    assert st.deck.kwargs["tooltip"]["html"] == "{tooltip_html}"
    assert st.height == dashboard.MAP_HEIGHT_PX
