#!/usr/bin/env python3.14
"""Streamlit dashboard for butterfly spatial heatmap exploration."""

from __future__ import annotations

import hashlib
import inspect
import math
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.visuals.spatial_heatmap_dashboard import query


DEFAULT_GRID_PATH = Path("datasets/insecta/lepidoptera/dashboard/butterfly_grid_bins.parquet")
PAGE_TITLE = "Butterfly Dashboard"
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
DEFAULT_SHARE_HEATMAP_POINT_LIMIT = 50_000
DEFAULT_MAX_SHARE_HEATMAPS = 12
MAP_HEIGHT_PX = 1_180
SHARE_HEATMAP_PANEL_HEIGHT_PX = 320
DOMINANT_CATEGORY_MODE = "Dominant category map"
CATEGORY_SHARE_HEATMAPS_MODE = "Compare category heatmaps"
SINGLE_COLOR_MODE = "Single-color bubbles"
PIECHART_COMPOSITION_MODE = "Piechart composition markers"
SHARE_HEATMAP_MODE = "Selected-category share heatmap"
MAP_DISPLAY_MODES = [
    DOMINANT_CATEGORY_MODE,
    PIECHART_COMPOSITION_MODE,
    CATEGORY_SHARE_HEATMAPS_MODE,
    SINGLE_COLOR_MODE,
    SHARE_HEATMAP_MODE,
]
DOMINANT_POINT_RADIUS_SCALE: tuple[tuple[int, int], ...] = (
    (1, 3),
    (10, 5),
    (25, 6),
    (50, 7),
    (100, 8),
    (250, 9),
    (500, 10),
    (750, 11),
    (1_000, 12),
    (2_500, 14),
    (5_000, 16),
    (7_500, 18),
    (10_000, 20),
    (15_000, 22),
    (20_000, 24),
    (25_000, 26),
    (50_000, 28),
)
DOMINANT_POINT_MIN_RADIUS_PX = DOMINANT_POINT_RADIUS_SCALE[0][1]
DOMINANT_POINT_MAX_RADIUS_PX = DOMINANT_POINT_RADIUS_SCALE[-1][1]
PIE_ICON_CANVAS_PX = 128
PIE_ICON_MIN_SIZE_PX = 2
PIE_ICON_MAX_SIZE_PX = 6
OTHER_COLOR_HEX = "#808080"
COLOR_LEVEL_LABELS = {
    "Family": "family",
    "Genus": "genus",
    "Species": "species",
    "Scientific name": "scientificName",
}
CONSERVATION_SCOPE_LABELS = {
    "National EPBC": "national",
    "State / territory": "state",
}
CONSERVATION_OPTION_KEYS = {
    "national": "national_statuses",
    "state": "state_status_levels",
}
COORDINATE_PRECISION_LEVELS = {
    "Regional": 1,
    "Local": 2,
    "Coarse": 0,
}
FAMILY_COLORS = {
    "Hesperiidae": [220, 38, 38, 190],
    "Lycaenidae": [22, 163, 74, 190],
    "Nymphalidae": [37, 99, 235, 190],
    "Papilionidae": [234, 179, 8, 190],
    "Pieridae": [147, 51, 234, 190],
    "Riodinidae": [249, 115, 22, 190],
}
COLOR_LEVEL_DISPLAY_NAMES = {
    "family": "family",
    "genus": "genus",
    "species": "species",
    "scientificName": "scientific name",
}


def stable_color(value: str | None) -> list[int]:
    text = value or "not supplied"
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [80 + digest[0] % 150, 70 + digest[1] % 160, 90 + digest[2] % 140, 170]


def map_color(color_level: str | None, color_value: str | None) -> list[int]:
    if color_level == "family" and color_value in FAMILY_COLORS:
        return FAMILY_COLORS[color_value]
    return stable_color(color_value)


def point_radius(record_count: int | float | None) -> float:
    count = max(float(record_count or 0), 1.0)
    return min(140_000.0, 18_000.0 + math.sqrt(count) * 4_500.0)


def dominant_point_radius(total_record_count: int | float | None) -> int:
    count = max(float(total_record_count or 0), 1.0)
    first_count, first_radius = DOMINANT_POINT_RADIUS_SCALE[0]
    if count <= first_count:
        return first_radius

    for (lower_count, lower_radius), (upper_count, upper_radius) in zip(
        DOMINANT_POINT_RADIUS_SCALE,
        DOMINANT_POINT_RADIUS_SCALE[1:],
    ):
        if count <= upper_count:
            position = (count - lower_count) / (upper_count - lower_count)
            radius = lower_radius + position * (upper_radius - lower_radius)
            return round(radius)

    return DOMINANT_POINT_MAX_RADIUS_PX


def color_to_hex(color: list[int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in color[:3])


def pie_icon_size(total_record_count: int | float | None) -> int:
    count = max(float(total_record_count or 0), 1.0)
    scale = min(math.log10(count) / 4.0, 1.0)
    return round(PIE_ICON_MIN_SIZE_PX + scale * (PIE_ICON_MAX_SIZE_PX - PIE_ICON_MIN_SIZE_PX))


def pie_slice_color(color_level: str | None, value: str | None) -> str:
    if value == "Other":
        return OTHER_COLOR_HEX
    return color_to_hex(map_color(color_level, value))


def pie_slice_path(
    start_angle: float,
    end_angle: float,
    *,
    center: float,
    radius: float,
) -> str:
    start_x = center + radius * math.cos(start_angle)
    start_y = center + radius * math.sin(start_angle)
    end_x = center + radius * math.cos(end_angle)
    end_y = center + radius * math.sin(end_angle)
    large_arc = 1 if end_angle - start_angle > math.pi else 0
    return (
        f"M {center:.3f} {center:.3f} "
        f"L {start_x:.3f} {start_y:.3f} "
        f"A {radius:.3f} {radius:.3f} 0 {large_arc} 1 {end_x:.3f} {end_y:.3f} Z"
    )


def build_pie_svg(composition: list[dict[str, Any]], *, color_level: str) -> str:
    center = PIE_ICON_CANVAS_PX / 2
    radius = center - 4
    start_angle = -math.pi / 2
    paths: list[str] = []
    for index, item in enumerate(composition):
        share = max(float(item.get("share") or 0), 0.0)
        if share <= 0:
            continue
        end_angle = start_angle + share * math.tau
        if index == len(composition) - 1:
            end_angle = -math.pi / 2 + math.tau - 0.000001
        color = pie_slice_color(color_level, item.get("value"))
        path = pie_slice_path(start_angle, end_angle, center=center, radius=radius)
        paths.append(f'<path d="{path}" fill="{color}" stroke="#ffffff" stroke-width="2"/>')
        start_angle = end_angle

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PIE_ICON_CANVAS_PX}" '
        f'height="{PIE_ICON_CANVAS_PX}" viewBox="0 0 {PIE_ICON_CANVAS_PX} {PIE_ICON_CANVAS_PX}">'
        f'<circle cx="{center}" cy="{center}" r="{radius}" fill="#ffffff" opacity="0.85"/>'
        + "".join(paths)
        + f'<circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="#111827" stroke-width="2"/>'
        + "</svg>"
    )


def pie_svg_data_url(svg: str) -> str:
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


def add_visual_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "color": map_color(row.get("color_level"), row.get("color_value")),
            "radius": point_radius(row.get("record_count")),
        }
        for row in rows
    ]


def add_category_share_heatmap_visual_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visual_rows = []
    for row in rows:
        color = map_color(row.get("color_level"), row.get("color_value")).copy()
        color[3] = share_alpha(row.get("share"))
        visual_rows.append(
            {
                **row,
                "color": color,
                "radius": point_radius(row.get("total_cell_records")),
                "share_percent": f"{float(row.get('share') or 0) * 100:.1f}%",
            }
        )
    return visual_rows


def add_dominant_category_visual_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visual_rows = []
    for row in rows:
        color_level = row.get("color_level")
        composition = row.get("composition") or []
        dominant = composition[0] if composition else {}
        dominant_value = dominant.get("value") or "Not supplied"
        dominant_count = int(dominant.get("record_count") or 0)
        dominant_share = float(dominant.get("share") or 0)
        color = map_color(color_level, dominant_value).copy()
        color[3] = share_alpha(dominant_share)
        color_level_label = COLOR_LEVEL_DISPLAY_NAMES.get(str(color_level), "category")
        total_records = int(row.get("total_record_count") or 0)
        share_percent = f"{dominant_share * 100:.1f}%"
        visual_rows.append(
            {
                **row,
                "dominant_value": dominant_value,
                "dominant_record_count": dominant_count,
                "dominant_share": dominant_share,
                "dominant_share_percent": share_percent,
                "fill_color": color,
                "radius_pixels": dominant_point_radius(total_records),
                "tooltip": (
                    f"Dominant {color_level_label} {dominant_value}: "
                    f"{share_percent} of {total_records:,} records\n"
                    f"Dominant records: {dominant_count:,}\n"
                    f"Composition:\n{row.get('composition_text') or ''}"
                ),
            }
        )
    return visual_rows


def add_piechart_visual_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visual_rows = []
    for row in rows:
        svg = build_pie_svg(row.get("composition") or [], color_level=row["color_level"])
        visual_rows.append(
            {
                **row,
                "icon_data": {
                    "url": pie_svg_data_url(svg),
                    "width": PIE_ICON_CANVAS_PX,
                    "height": PIE_ICON_CANVAS_PX,
                    "anchorX": PIE_ICON_CANVAS_PX // 2,
                    "anchorY": PIE_ICON_CANVAS_PX // 2,
                },
                "icon_size": pie_icon_size(row.get("total_record_count")),
            }
        )
    return visual_rows


def share_alpha(share: int | float | None) -> int:
    value = max(0.0, min(float(share or 0), 1.0))
    return int(55 + value * 175)


def add_share_heatmap_visual_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visual_rows = []
    for row in rows:
        color = map_color(row.get("color_level"), row.get("color_value")).copy()
        color[3] = share_alpha(row.get("share"))
        visual_rows.append(
            {
                **row,
                "color": color,
                "radius": point_radius(row.get("record_count")),
                "share_percent": f"{float(row.get('share') or 0) * 100:.1f}%",
            }
        )
    return visual_rows


def filter_mode(prefix: str, values: list[str], st: Any) -> tuple[list[str], list[str]]:
    mode = st.radio(
        f"{prefix} mode",
        ["Include", "Exclude"],
        horizontal=True,
        key=f"{prefix.lower()}_mode",
    )
    selected = st.multiselect(prefix, values, key=f"{prefix.lower()}_values")
    return (selected, []) if mode == "Include" else ([], selected)


def state_preset_values(preset: str, states: list[str]) -> list[str]:
    if preset == "East coast":
        return [state for state in EAST_COAST_STATES if state in states]
    if preset == "Mainland":
        return [state for state in MAINLAND_STATES if state in states]
    if preset == "All":
        return states
    return []


def state_selector(
    states: list[str],
    st: Any,
    session_state: Any,
) -> tuple[list[str], list[str]]:
    if "state_values" in session_state:
        session_state["state_values"] = [
            state for state in session_state["state_values"] if state in states
        ]

    def apply_state_preset() -> None:
        preset = session_state.get("state_preset", "Custom")
        session_state["state_values"] = state_preset_values(preset, states)

    mode = st.radio("State mode", ["Include", "Exclude"], horizontal=True, key="state_mode")
    selected = st.multiselect("State/territory", states, key="state_values")
    st.selectbox(
        "State preset",
        ["Custom", "East coast", "Mainland", "All"],
        index=0,
        key="state_preset",
        on_change=apply_state_preset,
    )
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


def build_partial_slicer_state(
    options: dict[str, list[Any]],
    family_st: Any,
    state_st: Any,
    year_st: Any,
    session_state: Any,
) -> query.SlicerState:
    include_families, exclude_families = filter_mode("Family", options["families"], family_st)
    include_states, exclude_states = state_selector(options["states"], state_st, session_state)
    years = [int(year) for year in options["years"] if year is not None]
    year_min = min(years) if years else None
    year_max = max(years) if years else None
    selected_range = year_st.slider(
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


def coordinate_precision_selector(st: Any) -> int:
    selected_label = st.selectbox(
        "Coordinate precision",
        list(COORDINATE_PRECISION_LEVELS),
        index=0,
        key="coordinate_precision_v1",
    )
    return COORDINATE_PRECISION_LEVELS[selected_label]


def conservation_selector(
    options: dict[str, list[Any]],
    st: Any,
) -> tuple[str | None, list[str]]:
    selected_label = st.selectbox(
        "Listing authority",
        list(CONSERVATION_SCOPE_LABELS),
        key="conservation_scope_v1",
    )
    scope = CONSERVATION_SCOPE_LABELS[selected_label]
    option_key = CONSERVATION_OPTION_KEYS[scope]
    statuses = [str(value) for value in options.get(option_key, []) if value is not None]
    selected_statuses = st.multiselect(
        "Conservation status",
        statuses,
        key="conservation_status_values_v1",
    )
    if not selected_statuses:
        return None, []
    return scope, selected_statuses


def query_with_coordinate_precision(
    query_function: Any,
    *args: Any,
    coordinate_decimals: int | None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    parameters = inspect.signature(query_function).parameters
    accepts_coordinate_decimals = "coordinate_decimals" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_coordinate_decimals:
        kwargs["coordinate_decimals"] = coordinate_decimals
    return query_function(*args, **kwargs)


def max_share_heatmaps_selector(st: Any) -> int:
    return int(
        st.number_input(
            "Max share heatmaps",
            min_value=1,
            max_value=24,
            value=DEFAULT_MAX_SHARE_HEATMAPS,
            step=1,
        )
    )


def color_lock_selector(st: Any) -> str | None:
    if not st.checkbox("Lock color level", value=False):
        return None
    selected_label = st.selectbox(
        "Color dots by",
        list(COLOR_LEVEL_LABELS),
        index=1,
    )
    return COLOR_LEVEL_LABELS[selected_label]


def map_display_selector(st: Any) -> str:
    return st.selectbox("Map display", MAP_DISPLAY_MODES, index=0, key="map_display_mode_v4")


def share_focus_selector(st: Any, options: list[dict[str, Any]]) -> str | None:
    if not options:
        st.info("No focus values available for the active color level.")
        return None
    labels = {
        f"{option['value']} ({int(option['record_count']):,})": str(option["value"])
        for option in options
    }
    selected = st.selectbox("Share focus", list(labels))
    return labels[selected]


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
                "{color_level}: {color_value}\n"
                "Family: {family}\n"
                "Genus: {genus}\n"
                "Species: {species}\n"
                "Scientific name: {scientificName}\n"
                "State: {stateProvince}\n"
                "Years: {year_range}\n"
                "Records: {record_count}"
            )
        },
    )
    st.pydeck_chart(deck, width="stretch", height=MAP_HEIGHT_PX)


def render_share_heatmap(rows: list[dict[str, Any]], st: Any, pdk: Any) -> None:
    map_rows = add_share_heatmap_visual_fields(rows)
    layer = pdk.Layer(
        "ScatterplotLayer",
        map_rows,
        get_position="[lon_bin, lat_bin]",
        get_radius="radius",
        get_fill_color="color",
        pickable=True,
        opacity=0.85,
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
                "{color_level}: {color_value}\n"
                "Focus records: {record_count}\n"
                "Total records here: {total_cell_records}\n"
                "Share: {share_percent}\n"
                "State: {stateProvince}\n"
                "Top values:\n{composition_text}"
            )
        },
    )
    st.pydeck_chart(deck, width="stretch", height=MAP_HEIGHT_PX)


def rows_by_color_value(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["color_value"]), []).append(row)
    return grouped


def render_category_share_heatmaps(rows: list[dict[str, Any]], st: Any, pdk: Any) -> None:
    grouped_rows = rows_by_color_value(rows)
    category_items = list(grouped_rows.items())
    for index in range(0, len(category_items), 2):
        columns = st.columns(2)
        for column, (color_value, category_rows) in zip(
            columns,
            category_items[index : index + 2],
            strict=False,
        ):
            total = int(category_rows[0].get("category_total_records") or 0)
            column.subheader(f"{color_value} ({total:,})")
            map_rows = add_category_share_heatmap_visual_fields(category_rows)
            layer = pdk.Layer(
                "ScatterplotLayer",
                map_rows,
                get_position="[lon_bin, lat_bin]",
                get_radius="radius",
                get_fill_color="color",
                pickable=True,
                opacity=0.85,
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
                        "{color_level}: {color_value}\n"
                        "Records: {record_count}\n"
                        "Total records here: {total_cell_records}\n"
                        "Share: {share_percent}"
                    )
                },
            )
            column.pydeck_chart(deck, width="stretch", height=SHARE_HEATMAP_PANEL_HEIGHT_PX)


def render_dominant_category_map(rows: list[dict[str, Any]], st: Any, pdk: Any) -> None:
    map_rows = add_dominant_category_visual_fields(rows)
    layer = pdk.Layer(
        "ScatterplotLayer",
        map_rows,
        get_position="[lon_bin, lat_bin]",
        get_radius="radius_pixels",
        radius_units="pixels",
        radius_scale=1,
        radius_min_pixels=DOMINANT_POINT_MIN_RADIUS_PX,
        radius_max_pixels=DOMINANT_POINT_MAX_RADIUS_PX,
        get_fill_color="fill_color",
        get_line_color=[17, 24, 39, 160],
        line_width_min_pixels=1,
        pickable=True,
        stroked=True,
        opacity=0.9,
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
        tooltip={"text": "{tooltip}"},
    )
    st.pydeck_chart(deck, width="stretch", height=MAP_HEIGHT_PX)


def render_piechart_composition(rows: list[dict[str, Any]], st: Any, pdk: Any) -> None:
    map_rows = add_piechart_visual_fields(rows)
    layer = pdk.Layer(
        "IconLayer",
        map_rows,
        get_position="[lon_bin, lat_bin]",
        get_icon="icon_data",
        get_size="icon_size",
        size_units="pixels",
        size_scale=1,
        pickable=True,
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
                "{color_level} composition\n"
                "Total records: {total_record_count}\n"
                "{composition_text}"
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

    st.set_page_config(page_title=PAGE_TITLE, layout="wide")
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
    st.title(PAGE_TITLE)
    max_controls = st.sidebar.container()
    family_controls = st.sidebar.container()
    genus_controls = st.sidebar.container()
    species_controls = st.sidebar.container()
    conservation_controls = st.sidebar.container()
    state_controls = st.sidebar.container()
    year_controls = st.sidebar.container()
    display_controls = st.sidebar.container()
    config = st.sidebar.container()
    summary = st.sidebar.container()
    grid_path = Path(config.text_input("Grid bins parquet", value=str(DEFAULT_GRID_PATH)))
    if not grid_path.exists():
        st.error(f"Missing grid bins: {grid_path}")
        st.stop()

    base_options = query.option_values(grid_path, query.SlicerState())
    map_point_limit = map_point_limit_selector(max_controls)
    coordinate_decimals = coordinate_precision_selector(max_controls)
    locked_color_dimension = color_lock_selector(max_controls)
    map_display_mode = map_display_selector(max_controls)
    max_share_heatmaps = (
        max_share_heatmaps_selector(max_controls)
        if map_display_mode == CATEGORY_SHARE_HEATMAPS_MODE
        else DEFAULT_MAX_SHARE_HEATMAPS
    )
    conservation_scope, conservation_statuses = conservation_selector(
        base_options,
        conservation_controls,
    )
    conservation_slicers = query.SlicerState(
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    family_options = query.option_values(grid_path, conservation_slicers)["families"]
    include_families, exclude_families = filter_mode(
        "Family",
        family_options,
        family_controls,
    )
    family_slicers = query.SlicerState(
        include_families=include_families,
        exclude_families=exclude_families,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    genus_options = query.option_values(grid_path, family_slicers)["genera"]
    include_genera, exclude_genera = filter_mode("Genus", genus_options, genus_controls)
    genus_slicers = query.SlicerState(
        include_families=include_families,
        exclude_families=exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    species_options = query.option_values(grid_path, genus_slicers)["species"]
    include_species, exclude_species = filter_mode("Species", species_options, species_controls)
    species_slicers = query.SlicerState(
        include_families=include_families,
        exclude_families=exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        include_species=include_species,
        exclude_species=exclude_species,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    state_options = query.option_values(grid_path, species_slicers)["states"]
    include_states, exclude_states = state_selector(
        state_options,
        state_controls,
        st.session_state,
    )
    state_slicers = query.SlicerState(
        include_families=include_families,
        exclude_families=exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        include_species=include_species,
        exclude_species=exclude_species,
        include_states=include_states,
        exclude_states=exclude_states,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    year_options = query.option_values(grid_path, state_slicers)["years"]
    years_for_slider = [int(year) for year in year_options if year is not None]
    year_min = min(years_for_slider) if years_for_slider else None
    year_max = max(years_for_slider) if years_for_slider else None
    selected_range = year_controls.slider(
        "Year range",
        min_value=year_min or 0,
        max_value=year_max or 0,
        value=(year_min or 0, year_max or 0),
        disabled=not years_for_slider,
    )
    active_year_min, active_year_max = active_year_bounds(
        years_for_slider,
        selected_range,
    )
    show_year_comparison = display_controls.checkbox("Show year comparison", value=False)
    show_filtered_rows = display_controls.checkbox("Show filtered rows", value=False)
    slicers = query.SlicerState(
        include_families=include_families,
        exclude_families=exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        include_species=include_species,
        exclude_species=exclude_species,
        include_states=include_states,
        exclude_states=exclude_states,
        year_min=active_year_min,
        year_max=active_year_max,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    filtered_options = query.option_values(grid_path, slicers)
    summary.subheader("Summary statistics")
    summary.caption(
        f"Families: {len(filtered_options['families'])} | "
        f"Genera: {len(filtered_options['genera'])} | "
        f"Species: {len(filtered_options['species'])} | "
        f"States: {len(filtered_options['states'])}"
    )

    focus_value = None
    if map_display_mode == SHARE_HEATMAP_MODE:
        focus_options = query.color_value_options(
            grid_path,
            slicers,
            locked_color_dimension=locked_color_dimension,
        )
        focus_value = share_focus_selector(max_controls, focus_options)

    share_limit_per_category = min(map_point_limit, DEFAULT_SHARE_HEATMAP_POINT_LIMIT)
    if map_display_mode == DOMINANT_CATEGORY_MODE:
        rows = query_with_coordinate_precision(
            query.query_composition_markers,
            grid_path,
            slicers,
            coordinate_decimals=coordinate_decimals,
            limit=map_point_limit,
            locked_color_dimension=locked_color_dimension,
        )
    elif map_display_mode == CATEGORY_SHARE_HEATMAPS_MODE:
        rows = query_with_coordinate_precision(
            query.query_all_share_heatmap_bins,
            grid_path,
            slicers,
            coordinate_decimals=coordinate_decimals,
            limit_per_category=share_limit_per_category,
            locked_color_dimension=locked_color_dimension,
            max_categories=max_share_heatmaps,
        )
    elif map_display_mode == PIECHART_COMPOSITION_MODE:
        rows = query_with_coordinate_precision(
            query.query_composition_markers,
            grid_path,
            slicers,
            coordinate_decimals=coordinate_decimals,
            limit=map_point_limit,
            locked_color_dimension=locked_color_dimension,
        )
    elif map_display_mode == SHARE_HEATMAP_MODE and focus_value:
        rows = query_with_coordinate_precision(
            query.query_share_heatmap_bins,
            grid_path,
            slicers,
            coordinate_decimals=coordinate_decimals,
            focus_value=focus_value,
            limit=map_point_limit,
            locked_color_dimension=locked_color_dimension,
        )
    elif map_display_mode == SHARE_HEATMAP_MODE:
        rows = []
    else:
        rows = query_with_coordinate_precision(
            query.query_grid_bins,
            grid_path,
            slicers,
            coordinate_decimals=coordinate_decimals,
            limit=map_point_limit,
            locked_color_dimension=locked_color_dimension,
        )
    years = query.year_summary(grid_path, slicers)
    matching_records = query.mapped_record_count(grid_path, slicers)
    if map_display_mode in {DOMINANT_CATEGORY_MODE, PIECHART_COMPOSITION_MODE}:
        total_records = sum(int(row["total_record_count"]) for row in rows)
    else:
        total_records = sum(int(row["record_count"]) for row in rows)
    point_based_modes = {
        DOMINANT_CATEGORY_MODE,
        CATEGORY_SHARE_HEATMAPS_MODE,
        PIECHART_COMPOSITION_MODE,
    }
    summary.metric(
        "Map points" if map_display_mode in point_based_modes else "Map bins",
        f"{len(rows):,}",
    )
    if map_display_mode == SHARE_HEATMAP_MODE:
        summary.metric("Focused records", f"{total_records:,}")
    else:
        summary.metric("Visible records", f"{total_records:,}")
    summary.metric("Matching records", f"{matching_records:,}")
    summary.metric("Years", f"{len(years):,}")
    if map_display_mode == CATEGORY_SHARE_HEATMAPS_MODE:
        shown_categories = len(rows_by_color_value(rows))
        summary.metric("Heatmaps", f"{shown_categories:,}")
        if shown_categories >= max_share_heatmaps:
            summary.warning(
                f"Showing the top {max_share_heatmaps:,} active categories by record count. "
                "Narrow the taxonomy slicers to inspect more categories."
            )
        if len(rows) >= shown_categories * share_limit_per_category:
            summary.warning(
                "Per-heatmap point cap may be hiding lower-count coordinates. "
                "Narrow the slicers for more detail."
            )
    if (
        map_display_mode in {DOMINANT_CATEGORY_MODE, PIECHART_COMPOSITION_MODE}
        and total_records < matching_records
    ):
        summary.warning(
            f"Map point cap is hiding {matching_records - total_records:,} matching records. "
            "Increase Max map points or narrow the slicers."
        )
    elif map_display_mode == SHARE_HEATMAP_MODE and len(rows) >= map_point_limit:
        summary.warning(
            "Map point cap may be hiding focused map points. "
            "Increase Max map points or narrow the slicers."
        )
    elif map_display_mode != SHARE_HEATMAP_MODE and total_records < matching_records:
        summary.warning(
            f"Map point cap is hiding {matching_records - total_records:,} matching records. "
            "Increase Max map points or narrow the slicers."
        )

    if map_display_mode == DOMINANT_CATEGORY_MODE:
        render_dominant_category_map(rows, st, pdk)
    elif map_display_mode == CATEGORY_SHARE_HEATMAPS_MODE:
        render_category_share_heatmaps(rows, st, pdk)
    elif map_display_mode == PIECHART_COMPOSITION_MODE:
        render_piechart_composition(rows, st, pdk)
    elif map_display_mode == SHARE_HEATMAP_MODE:
        render_share_heatmap(rows, st, pdk)
    else:
        render_map(rows, st, pdk)
    if show_year_comparison:
        st.subheader("Year comparison")
        st.bar_chart(years, x="year", y="record_count")
    if show_filtered_rows:
        st.subheader("Filtered map rows")
        st.dataframe(rows, use_container_width=True)


if __name__ == "__main__":
    main()
