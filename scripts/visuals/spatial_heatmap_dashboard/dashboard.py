#!/usr/bin/env python3.14
"""Streamlit dashboard for butterfly spatial heatmap exploration."""

from __future__ import annotations

import hashlib
import html
import importlib.util
import inspect
import json
import math
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

def load_query_module() -> Any:
    module_path = Path(__file__).with_name("query.py")
    module_name = "_butterfly_dashboard_query"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load dashboard query module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "query_sa3_composition_shapes"):
        raise AttributeError(
            f"Dashboard query module at {module_path} does not expose "
            "query_sa3_composition_shapes."
        )
    return module


query = load_query_module()


def default_data_path(deployment_path: str, source_path: str) -> Path:
    local_path = Path(deployment_path)
    return local_path if local_path.exists() else Path(source_path)


DEFAULT_SA3_BINS_PATH = default_data_path(
    "data/butterfly_sa3_bins.parquet",
    "datasets/insecta/lepidoptera/dashboard/butterfly_sa3_bins.parquet",
)
DEFAULT_SA3_BOUNDARIES_PATH = default_data_path(
    "data/sa3_boundaries_2021.parquet",
    "datasets/insecta/lepidoptera/dashboard/sa3_boundaries_2021.parquet",
)
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
DEFAULT_SA3_POLYGON_LIMIT = 500
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
]
SA3_POLYGON_MIN_ALPHA = 45
SA3_POLYGON_MAX_ALPHA = 220
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


def build_dominant_tooltip_html(
    *,
    color_level_label: str,
    dominant_value: str,
    share_percent: str,
    total_records: int,
    dominant_count: int,
    composition_text: str,
    pie_url: str,
) -> str:
    composition_html = html.escape(composition_text or "").replace("\n", "<br/>")
    return (
        '<div style="font-family:Inter,Arial,sans-serif;line-height:1.35;">'
        '<div style="font-weight:700;margin-bottom:6px;">'
        f"Dominant {html.escape(color_level_label)}: {html.escape(dominant_value)}"
        "</div>"
        '<div style="display:flex;gap:10px;align-items:flex-start;">'
        f'<img src="{pie_url}" width="96" height="96" '
        'style="flex:0 0 auto;border-radius:50%;background:#ffffff;" />'
        '<div style="min-width:160px;">'
        f"<div>Share: <b>{html.escape(share_percent)}</b></div>"
        f"<div>Total records: <b>{total_records:,}</b></div>"
        f"<div>Dominant records: <b>{dominant_count:,}</b></div>"
        "</div>"
        "</div>"
        '<div style="font-weight:700;margin-top:8px;">Composition</div>'
        '<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        'font-size:12px;white-space:normal;">'
        f"{composition_html}"
        "</div>"
        "</div>"
    )


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
        composition_text = row.get("composition_text") or ""
        pie_url = pie_svg_data_url(
            build_pie_svg(composition, color_level=str(color_level or "category"))
        )
        tooltip_html = build_dominant_tooltip_html(
            color_level_label=color_level_label,
            dominant_value=dominant_value,
            share_percent=share_percent,
            total_records=total_records,
            dominant_count=dominant_count,
            composition_text=composition_text,
            pie_url=pie_url,
        )
        visual_rows.append(
            {
                **row,
                "dominant_value": dominant_value,
                "dominant_record_count": dominant_count,
                "dominant_share": dominant_share,
                "dominant_share_percent": share_percent,
                "fill_color": color,
                "radius_pixels": dominant_point_radius(total_records),
                "tooltip_pie_url": pie_url,
                "tooltip_html": tooltip_html,
                "tooltip": (
                    f"Dominant {color_level_label} {dominant_value}: "
                    f"{share_percent} of {total_records:,} records\n"
                    f"Dominant records: {dominant_count:,}\n"
                    f"Composition:\n{composition_text}"
                ),
            }
        )
    return visual_rows


def sa3_polygon_alpha(
    record_count: int | float | None,
    *,
    max_record_count: int | float | None,
) -> int:
    count = max(float(record_count or 0), 1.0)
    max_count = max(float(max_record_count or 0), 1.0)
    if max_count <= 1:
        return SA3_POLYGON_MIN_ALPHA
    position = (count - 1.0) / (max_count - 1.0)
    position = max(0.0, min(position, 1.0))
    return round(
        SA3_POLYGON_MIN_ALPHA
        + position * (SA3_POLYGON_MAX_ALPHA - SA3_POLYGON_MIN_ALPHA)
    )


def build_sa3_tooltip_html(row: dict[str, Any], pie_url: str) -> str:
    color_level = str(row.get("color_level") or "category")
    color_level_label = COLOR_LEVEL_DISPLAY_NAMES.get(color_level, "category")
    dominant_value = str(row.get("dominant_value") or "Not supplied")
    dominant_count = int(row.get("dominant_record_count") or 0)
    dominant_share = float(row.get("dominant_share") or 0)
    total_records = int(row.get("total_record_count") or 0)
    composition = sorted(
        row.get("composition") or [],
        key=lambda item: (
            -int(item.get("record_count") or 0),
            str(item.get("value") or ""),
        ),
    )
    table_rows = "".join(
        "<tr>"
        f"<td style=\"padding:2px 10px 2px 0;\">{html.escape(str(item.get('value') or 'Not supplied'))}</td>"
        f"<td style=\"padding:2px 10px;text-align:right;\">{int(item.get('record_count') or 0):,}</td>"
        f"<td style=\"padding:2px 0;text-align:right;\">{float(item.get('share') or 0) * 100:.1f}%</td>"
        "</tr>"
        for item in composition
    )
    return (
        '<div style="font-family:Inter,Arial,sans-serif;line-height:1.35;">'
        '<div style="font-weight:700;margin-bottom:6px;">'
        f"SA3: {html.escape(str(row.get('sa3_name_2021') or 'Unknown SA3'))}"
        "</div>"
        '<div style="font-weight:700;margin-bottom:6px;">'
        f"Dominant {html.escape(color_level_label)}: {html.escape(dominant_value)}"
        "</div>"
        '<div style="display:flex;gap:10px;align-items:flex-start;">'
        f'<img src="{pie_url}" width="96" height="96" '
        'style="flex:0 0 auto;border-radius:50%;background:#ffffff;" />'
        '<div style="min-width:170px;">'
        f"<div>Dominant share: <b>{dominant_share * 100:.1f}%</b></div>"
        f"<div>Total records: <b>{total_records:,}</b></div>"
        f"<div>Dominant records: <b>{dominant_count:,}</b></div>"
        "</div>"
        "</div>"
        f'<div style="font-weight:700;margin-top:8px;">Composition by {html.escape(color_level_label)}</div>'
        '<table style="border-collapse:collapse;font-size:12px;width:100%;">'
        '<thead><tr>'
        '<th style="padding:2px 10px 3px 0;text-align:left;">Value</th>'
        '<th style="padding:2px 10px 3px;text-align:right;">Records</th>'
        '<th style="padding:2px 0 3px;text-align:right;">Share</th>'
        "</tr></thead>"
        f"<tbody>{table_rows}</tbody>"
        "</table>"
        "</div>"
    )


def add_sa3_polygon_visual_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    max_record_count = max(
        (int(row.get("total_record_count") or 0) for row in rows),
        default=1,
    )
    visual_rows = []
    for row in rows:
        color_level = row.get("color_level")
        dominant_value = row.get("dominant_value") or "Not supplied"
        color = map_color(color_level, str(dominant_value)).copy()
        color[3] = sa3_polygon_alpha(
            row.get("total_record_count"),
            max_record_count=max_record_count,
        )
        composition = sorted(
            row.get("composition") or [],
            key=lambda item: (
                -int(item.get("record_count") or 0),
                str(item.get("value") or ""),
            ),
        )
        pie_url = pie_svg_data_url(
            build_pie_svg(
                composition,
                color_level=str(color_level or "category"),
            )
        )
        visual_rows.append(
            {
                **row,
                "composition": composition,
                "fill_color": color,
                "line_color": [17, 24, 39, 150],
                "tooltip_pie_url": pie_url,
                "tooltip_html": build_sa3_tooltip_html(row, pie_url),
            }
        )
    return visual_rows


def sa3_rows_to_geojson_features(rows: list[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for row in rows:
        geometry = row.get("geometry_geojson")
        if isinstance(geometry, str):
            geometry = json.loads(geometry)
        properties = {key: value for key, value in row.items() if key != "geometry_geojson"}
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "tooltip_html": row.get("tooltip_html", ""),
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


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


def slicer_state(**kwargs: Any) -> query.SlicerState:
    parameters = inspect.signature(query.SlicerState).parameters
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    supported_kwargs = (
        kwargs
        if accepts_kwargs
        else {key: value for key, value in kwargs.items() if key in parameters}
    )
    return query.SlicerState(**supported_kwargs)


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
    return slicer_state(
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
            "Max SA3 polygons",
            min_value=1,
            max_value=500,
            value=DEFAULT_SA3_POLYGON_LIMIT,
            step=10,
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
        tooltip={
            "html": "{tooltip_html}",
            "style": {
                "backgroundColor": "rgba(17, 24, 39, 0.96)",
                "border": "1px solid rgba(255, 255, 255, 0.18)",
                "borderRadius": "6px",
                "boxShadow": "0 12px 32px rgba(15, 23, 42, 0.28)",
                "color": "#f9fafb",
                "maxWidth": "360px",
                "padding": "10px",
            },
        },
    )
    st.pydeck_chart(deck, width="stretch", height=MAP_HEIGHT_PX)


def render_sa3_dominant_map(rows: list[dict[str, Any]], st: Any, pdk: Any) -> None:
    map_rows = add_sa3_polygon_visual_fields(rows)
    features = sa3_rows_to_geojson_features(map_rows)
    layer = pdk.Layer(
        "GeoJsonLayer",
        features,
        pickable=True,
        auto_highlight=True,
        stroked=True,
        filled=True,
        get_fill_color="properties.fill_color",
        get_line_color="properties.line_color",
        line_width_min_pixels=1,
        opacity=1.0,
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
            "html": "{tooltip_html}",
            "style": {
                "backgroundColor": "rgba(17, 24, 39, 0.96)",
                "border": "1px solid rgba(255, 255, 255, 0.18)",
                "borderRadius": "6px",
                "boxShadow": "0 12px 32px rgba(15, 23, 42, 0.28)",
                "color": "#f9fafb",
                "maxWidth": "380px",
                "padding": "10px",
            },
        },
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
    sa3_bins_path = Path(
        config.text_input("SA3 bins parquet", value=str(DEFAULT_SA3_BINS_PATH))
    )
    sa3_boundaries_path = Path(
        config.text_input("SA3 boundaries parquet", value=str(DEFAULT_SA3_BOUNDARIES_PATH))
    )
    if not sa3_bins_path.exists():
        st.error(f"Missing SA3 bins: {sa3_bins_path}")
        st.stop()
    if not sa3_boundaries_path.exists():
        st.error(f"Missing SA3 boundaries: {sa3_boundaries_path}")
        st.stop()

    base_options = query.option_values(sa3_bins_path, slicer_state())
    map_polygon_limit = map_point_limit_selector(max_controls)
    locked_color_dimension = color_lock_selector(max_controls)
    conservation_scope, conservation_statuses = conservation_selector(
        base_options,
        conservation_controls,
    )
    conservation_slicers = slicer_state(
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    family_options = query.option_values(sa3_bins_path, conservation_slicers)["families"]
    include_families, exclude_families = filter_mode(
        "Family",
        family_options,
        family_controls,
    )
    family_slicers = slicer_state(
        include_families=include_families,
        exclude_families=exclude_families,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    genus_options = query.option_values(sa3_bins_path, family_slicers)["genera"]
    include_genera, exclude_genera = filter_mode("Genus", genus_options, genus_controls)
    genus_slicers = slicer_state(
        include_families=include_families,
        exclude_families=exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    species_options = query.option_values(sa3_bins_path, genus_slicers)["species"]
    include_species, exclude_species = filter_mode("Species", species_options, species_controls)
    species_slicers = slicer_state(
        include_families=include_families,
        exclude_families=exclude_families,
        include_genera=include_genera,
        exclude_genera=exclude_genera,
        include_species=include_species,
        exclude_species=exclude_species,
        conservation_scope=conservation_scope,
        include_conservation_statuses=conservation_statuses,
    )
    state_options = query.option_values(sa3_bins_path, species_slicers)["states"]
    include_states, exclude_states = state_selector(
        state_options,
        state_controls,
        st.session_state,
    )
    state_slicers = slicer_state(
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
    year_options = query.option_values(sa3_bins_path, state_slicers)["years"]
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
    slicers = slicer_state(
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
    filtered_options = query.option_values(sa3_bins_path, slicers)
    summary.subheader("Summary statistics")
    summary.caption(
        f"Families: {len(filtered_options['families'])} | "
        f"Genera: {len(filtered_options['genera'])} | "
        f"Species: {len(filtered_options['species'])} | "
        f"States: {len(filtered_options['states'])}"
    )

    rows = query.query_sa3_composition_shapes(
        sa3_bins_path,
        sa3_boundaries_path,
        slicers,
        limit=map_polygon_limit,
        locked_color_dimension=locked_color_dimension,
    )
    years = query.year_summary(sa3_bins_path, slicers)
    matching_records = query.mapped_record_count(sa3_bins_path, slicers)
    total_records = sum(int(row["total_record_count"]) for row in rows)
    summary.metric("SA3 polygons", f"{len(rows):,}")
    summary.metric("Visible records", f"{total_records:,}")
    summary.metric("Matching records", f"{matching_records:,}")
    summary.metric("Years", f"{len(years):,}")
    if total_records < matching_records:
        summary.warning(
            f"SA3 polygon cap is hiding {matching_records - total_records:,} "
            "matching records. Increase Max SA3 polygons or narrow the slicers."
        )

    render_sa3_dominant_map(rows, st, pdk)
    if show_year_comparison:
        st.subheader("Year comparison")
        st.bar_chart(years, x="year", y="record_count")
    if show_filtered_rows:
        st.subheader("Filtered map rows")
        st.dataframe(rows, use_container_width=True)


if __name__ == "__main__":
    main()
