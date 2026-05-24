#!/usr/bin/env python3.14
"""Streamlit dashboard for butterfly spatial heatmap exploration."""

from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.visuals.spatial_heatmap_dashboard import query


DEFAULT_GRID_PATH = Path("datasets/insecta/lepidoptera/dashboard/butterfly_grid_bins.parquet")
EAST_COAST_STATES = [
    "Victoria",
    "New South Wales",
    "Australian Capital Territory",
    "Queensland",
]
MAINLAND_STATES = [
    "Australian Capital Territory",
    "New South Wales",
    "Northern Territory",
    "Queensland",
    "South Australia",
    "Victoria",
    "Western Australia",
]
CARTO_POSITRON_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
DEFAULT_MAP_POINT_LIMIT = 250_000
MAP_HEIGHT_PX = 820


def genus_color(genus: str | None) -> list[int]:
    text = genus or "not supplied"
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [80 + digest[0] % 150, 70 + digest[1] % 160, 90 + digest[2] % 140, 170]


def point_radius(record_count: int | float | None) -> float:
    count = max(float(record_count or 0), 1.0)
    return min(140_000.0, 18_000.0 + math.sqrt(count) * 4_500.0)


def add_visual_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "color": genus_color(row.get("genus")),
            "radius": point_radius(row.get("record_count")),
        }
        for row in rows
    ]


def filter_mode(prefix: str, values: list[str], st: Any) -> tuple[list[str], list[str]]:
    mode = st.radio(
        f"{prefix} mode",
        ["Include", "Exclude"],
        horizontal=True,
        key=f"{prefix.lower()}_mode",
    )
    selected = st.multiselect(prefix, values, key=f"{prefix.lower()}_values")
    return (selected, []) if mode == "Include" else ([], selected)


def state_selector(states: list[str], st: Any) -> tuple[list[str], list[str]]:
    preset = st.selectbox(
        "State preset",
        ["Custom", "East coast", "Mainland", "All"],
        index=0,
    )
    default_values: list[str] = []
    if preset == "East coast":
        default_values = [state for state in EAST_COAST_STATES if state in states]
    elif preset == "Mainland":
        default_values = [state for state in MAINLAND_STATES if state in states]
    elif preset == "All":
        default_values = states

    mode = st.radio("State mode", ["Include", "Exclude"], horizontal=True)
    selected = st.multiselect("State/territory", states, default=default_values)
    return (selected, []) if mode == "Include" else ([], selected)


def active_year_bounds(
    years: list[int],
    selected_range: tuple[int, int],
) -> tuple[int | None, int | None]:
    if not years:
        return None, None
    full_range = (min(years), max(years))
    if selected_range == full_range:
        return None, None
    return selected_range


def build_partial_slicer_state(options: dict[str, list[Any]], st: Any) -> query.SlicerState:
    include_families, exclude_families = filter_mode("Family", options["families"], st)
    include_states, exclude_states = state_selector(options["states"], st)
    years = [int(year) for year in options["years"] if year is not None]
    year_min = min(years) if years else None
    year_max = max(years) if years else None
    selected_range = st.slider(
        "Year range",
        min_value=year_min or 0,
        max_value=year_max or 0,
        value=(year_min or 0, year_max or 0),
        disabled=not years,
    )
    active_year_min, active_year_max = active_year_bounds(years, selected_range)
    return query.SlicerState(
        include_families=include_families,
        exclude_families=exclude_families,
        include_states=include_states,
        exclude_states=exclude_states,
        year_min=active_year_min,
        year_max=active_year_max,
    )


def map_point_limit_selector(st: Any) -> int:
    return int(
        st.number_input(
            "Max map points",
            min_value=10_000,
            max_value=1_000_000,
            value=DEFAULT_MAP_POINT_LIMIT,
            step=10_000,
        )
    )


def render_map(rows: list[dict[str, Any]], st: Any, pdk: Any) -> None:
    map_rows = add_visual_fields(rows)
    layer = pdk.Layer(
        "ScatterplotLayer",
        map_rows,
        get_position="[lon_bin, lat_bin]",
        get_radius="radius",
        get_fill_color="color",
        pickable=True,
        opacity=0.75,
    )
    deck = pdk.Deck(
        map_style=CARTO_POSITRON_STYLE,
        initial_view_state=pdk.ViewState(
            latitude=-25.3,
            longitude=134.5,
            zoom=3.2,
            pitch=0,
        ),
        layers=[layer],
        tooltip={
            "text": (
                "{species}\n"
                "{genus}\n"
                "{family}\n"
                "{stateProvince}, {year_range}\n"
                "Records: {record_count}"
            )
        },
    )
    st.pydeck_chart(deck, width="stretch", height=MAP_HEIGHT_PX)


def main() -> None:
    try:
        import pydeck as pdk
        import streamlit as st
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Install dashboard dependencies with: "
            "pip install streamlit pydeck"
        ) from exc

    st.set_page_config(page_title="Butterfly Spatial Heatmap", layout="wide")
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 1rem;
        }
        div[data-testid="stMetric"] {
            padding: 0.15rem 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("Butterfly Spatial Heatmap")
    grid_path = Path(
        st.sidebar.text_input("Grid bins parquet", value=str(DEFAULT_GRID_PATH))
    )
    if not grid_path.exists():
        st.error(f"Missing grid bins: {grid_path}")
        st.stop()
    map_point_limit = map_point_limit_selector(st.sidebar)
    show_year_comparison = st.sidebar.checkbox("Show year comparison", value=False)
    show_filtered_rows = st.sidebar.checkbox("Show filtered rows", value=False)

    base_options = query.option_values(grid_path, query.SlicerState())
    partial_slicers = build_partial_slicer_state(base_options, st.sidebar)
    genus_options = query.option_values(grid_path, partial_slicers)["genera"]
    include_genera, exclude_genera = filter_mode("Genus", genus_options, st.sidebar)
    genus_slicers = query.SlicerState(
        include_families=partial_slicers.include_families,
        exclude_families=partial_slicers.exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        include_states=partial_slicers.include_states,
        exclude_states=partial_slicers.exclude_states,
        year_min=partial_slicers.year_min,
        year_max=partial_slicers.year_max,
    )
    species_options = query.option_values(grid_path, genus_slicers)["species"]
    include_species, exclude_species = filter_mode("Species", species_options, st.sidebar)
    slicers = query.SlicerState(
        include_families=partial_slicers.include_families,
        exclude_families=partial_slicers.exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        include_species=include_species,
        exclude_species=exclude_species,
        include_states=partial_slicers.include_states,
        exclude_states=partial_slicers.exclude_states,
        year_min=partial_slicers.year_min,
        year_max=partial_slicers.year_max,
    )
    filtered_options = query.option_values(grid_path, slicers)
    st.sidebar.caption(
        f"Families: {len(filtered_options['families'])} | "
        f"Genera: {len(filtered_options['genera'])} | "
        f"Species: {len(filtered_options['species'])} | "
        f"States: {len(filtered_options['states'])}"
    )

    rows = query.query_grid_bins(grid_path, slicers, limit=map_point_limit)
    years = query.year_summary(grid_path, slicers)
    matching_records = query.mapped_record_count(grid_path, slicers)
    total_records = sum(int(row["record_count"]) for row in rows)
    st.sidebar.metric("Map bins", f"{len(rows):,}")
    st.sidebar.metric("Visible records", f"{total_records:,}")
    st.sidebar.metric("Matching records", f"{matching_records:,}")
    st.sidebar.metric("Years", f"{len(years):,}")
    if total_records < matching_records:
        st.sidebar.warning(
            f"Map point cap is hiding {matching_records - total_records:,} matching records. "
            "Increase Max map points or narrow the slicers."
        )

    render_map(rows, st, pdk)
    if show_year_comparison:
        st.subheader("Year comparison")
        st.bar_chart(years, x="year", y="record_count")
    if show_filtered_rows:
        st.subheader("Filtered map rows")
        st.dataframe(rows, use_container_width=True)


if __name__ == "__main__":
    main()
