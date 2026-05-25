"""DuckDB-backed slicer query helpers for spatial heatmap dashboards."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb


@dataclass(frozen=True)
class SlicerState:
    include_families: list[str] = field(default_factory=list)
    exclude_families: list[str] = field(default_factory=list)
    include_genera: list[str] = field(default_factory=list)
    exclude_genera: list[str] = field(default_factory=list)
    include_species: list[str] = field(default_factory=list)
    exclude_species: list[str] = field(default_factory=list)
    include_states: list[str] = field(default_factory=list)
    exclude_states: list[str] = field(default_factory=list)
    year_min: int | None = None
    year_max: int | None = None


FILTER_COLUMNS = {
    "family": ("include_families", "exclude_families"),
    "genus": ("include_genera", "exclude_genera"),
    "species": ("include_species", "exclude_species"),
    "stateProvince": ("include_states", "exclude_states"),
}
COLOR_DIMENSIONS = ("family", "genus", "species", "scientificName")
TAXONOMY_FILTERS = (
    ("species", "include_species", "exclude_species", "scientificName"),
    ("genus", "include_genera", "exclude_genera", "species"),
    ("family", "include_families", "exclude_families", "genus"),
)
GROUP_COLUMNS_BY_COLOR_DIMENSION = {
    "family": ["lat_bin", "lon_bin", "family", "stateProvince"],
    "genus": ["lat_bin", "lon_bin", "family", "genus", "stateProvince"],
    "species": ["lat_bin", "lon_bin", "family", "genus", "species", "stateProvince"],
    "scientificName": [
        "lat_bin",
        "lon_bin",
        "family",
        "genus",
        "species",
        "scientificName",
        "stateProvince",
    ],
}
DISPLAY_COLUMNS = ["family", "genus", "species", "scientificName", "stateProvince"]
SHARE_DISPLAY_COLUMNS_BY_COLOR_DIMENSION = {
    "family": ["family", "stateProvince"],
    "genus": ["family", "genus", "stateProvince"],
    "species": ["family", "genus", "species", "stateProvince"],
    "scientificName": DISPLAY_COLUMNS,
}


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def coordinate_source_sql(
    grid_path: Path,
    coordinate_decimals: int | None = None,
) -> str:
    source = f"read_parquet({sql_string(Path(grid_path).as_posix())})"
    if coordinate_decimals is None:
        return source

    decimals = max(int(coordinate_decimals), 0)
    return f"""
        (
            SELECT
                round(lat_bin, {decimals}) AS lat_bin,
                round(lon_bin, {decimals}) AS lon_bin,
                family,
                genus,
                species,
                scientificName,
                year,
                stateProvince,
                SUM(record_count) AS record_count,
                SUM(distinct_scientific_names) AS distinct_scientific_names,
                SUM(distinct_taxon_concepts) AS distinct_taxon_concepts,
                MIN(min_year) AS min_year,
                MAX(max_year) AS max_year
            FROM {source}
            GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
        )
    """


def value_list(values: list[str]) -> str:
    return "(" + ", ".join(sql_string(value) for value in values) + ")"


def filter_clauses(filters: SlicerState, *, skip_column: str | None = None) -> list[str]:
    clauses: list[str] = []

    for column, (include_attr, exclude_attr) in FILTER_COLUMNS.items():
        if column == skip_column:
            continue
        include_values = getattr(filters, include_attr)
        exclude_values = getattr(filters, exclude_attr)
        if include_values:
            clauses.append(f"{column} IN {value_list(include_values)}")
        if exclude_values:
            clauses.append(f"({column} IS NULL OR {column} NOT IN {value_list(exclude_values)})")

    if skip_column != "year":
        if filters.year_min is not None:
            clauses.append(f"year >= {int(filters.year_min)}")
        if filters.year_max is not None:
            clauses.append(f"year <= {int(filters.year_max)}")

    return clauses


def where_sql(filters: SlicerState, *, skip_column: str | None = None) -> str:
    clauses = filter_clauses(filters, skip_column=skip_column)
    return "WHERE " + " AND ".join(clauses) if clauses else ""


def color_dimension(
    filters: SlicerState,
    locked_color_dimension: str | None = None,
) -> str:
    if locked_color_dimension in COLOR_DIMENSIONS:
        return locked_color_dimension
    for _, include_attr, exclude_attr, dimension in TAXONOMY_FILTERS:
        if getattr(filters, include_attr) or getattr(filters, exclude_attr):
            return dimension
    return "family"


def grouped_select_columns(group_columns: list[str]) -> str:
    selected: list[str] = []
    for column in ["lat_bin", "lon_bin", *DISPLAY_COLUMNS]:
        selected.append(column if column in group_columns else f"NULL AS {column}")
    return ",\n                ".join(selected)


def share_display_select_columns(display_columns: list[str]) -> str:
    selected: list[str] = []
    for column in DISPLAY_COLUMNS:
        if column not in display_columns:
            selected.append(f"NULL AS {column}")
            continue
        selected.append(
            f"""
                CASE
                    WHEN COUNT(DISTINCT {column}) = 1 THEN MIN(CAST({column} AS VARCHAR))
                    WHEN COUNT(DISTINCT {column}) > 1 THEN 'multiple'
                    ELSE NULL
                END AS {column}
            """.strip()
        )
    return ",\n                ".join(selected)


def query_grid_bins(
    grid_path: Path,
    filters: SlicerState,
    *,
    limit: int = 250_000,
    locked_color_dimension: str | None = None,
    coordinate_decimals: int | None = None,
) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        color_by = color_dimension(filters, locked_color_dimension)
        group_columns = GROUP_COLUMNS_BY_COLOR_DIMENSION[color_by]
        group_sql = ", ".join(group_columns)
        source = coordinate_source_sql(grid_path, coordinate_decimals)
        query = f"""
            SELECT
                {grouped_select_columns(group_columns)},
                SUM(record_count) AS record_count,
                SUM(distinct_scientific_names) AS distinct_scientific_names,
                SUM(distinct_taxon_concepts) AS distinct_taxon_concepts,
                MIN(min_year) AS min_year,
                MAX(max_year) AS max_year,
                CASE
                    WHEN MIN(min_year) = MAX(max_year) THEN CAST(MIN(min_year) AS VARCHAR)
                    ELSE CAST(MIN(min_year) AS VARCHAR) || '-' || CAST(MAX(max_year) AS VARCHAR)
                END AS year_range,
                {sql_string(color_by)} AS color_level,
                COALESCE(CAST({color_by} AS VARCHAR), 'not supplied') AS color_value
            FROM {source}
            {where_sql(filters)}
            GROUP BY {group_sql}
            ORDER BY record_count DESC
            LIMIT {int(limit)}
        """
        result = con.execute(query)
        columns = [item[0] for item in result.description]
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    finally:
        con.close()


def color_value_options(
    grid_path: Path,
    filters: SlicerState,
    *,
    locked_color_dimension: str | None = None,
    limit: int = 5_000,
) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        color_by = color_dimension(filters, locked_color_dimension)
        result = con.execute(
            f"""
            SELECT
                COALESCE(CAST({color_by} AS VARCHAR), 'not supplied') AS value,
                SUM(record_count) AS record_count
            FROM read_parquet({sql_string(Path(grid_path).as_posix())})
            {where_sql(filters)}
            GROUP BY value
            ORDER BY record_count DESC, value
            LIMIT {int(limit)}
            """
        )
        columns = [item[0] for item in result.description]
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    finally:
        con.close()


def query_share_heatmap_bins(
    grid_path: Path,
    filters: SlicerState,
    *,
    focus_value: str,
    limit: int = 250_000,
    locked_color_dimension: str | None = None,
    coordinate_decimals: int | None = None,
    top_competitors: int = 5,
) -> list[dict[str, Any]]:
    if not focus_value:
        return []

    con = duckdb.connect(":memory:")
    try:
        color_by = color_dimension(filters, locked_color_dimension)
        source = coordinate_source_sql(grid_path, coordinate_decimals)
        filtered = f"""
            SELECT
                *,
                COALESCE(CAST({color_by} AS VARCHAR), 'not supplied') AS active_color_value
            FROM {source}
            {where_sql(filters)}
        """
        focus_clause = f"active_color_value = {sql_string(focus_value)}"
        display_columns = SHARE_DISPLAY_COLUMNS_BY_COLOR_DIMENSION[color_by]
        result = con.execute(
            f"""
            WITH filtered AS ({filtered}),
            cell_totals AS (
                SELECT lat_bin, lon_bin, SUM(record_count) AS total_cell_records
                FROM filtered
                GROUP BY lat_bin, lon_bin
            ),
            focus_counts AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    {share_display_select_columns(display_columns)},
                    SUM(record_count) AS record_count
                FROM filtered
                WHERE {focus_clause}
                GROUP BY lat_bin, lon_bin
            )
            SELECT
                focus_counts.lat_bin,
                focus_counts.lon_bin,
                focus_counts.family,
                focus_counts.genus,
                focus_counts.species,
                focus_counts.scientificName,
                focus_counts.stateProvince,
                focus_counts.record_count,
                cell_totals.total_cell_records,
                focus_counts.record_count::DOUBLE / cell_totals.total_cell_records AS share,
                {sql_string(color_by)} AS color_level,
                {sql_string(focus_value)} AS color_value
            FROM focus_counts
            INNER JOIN cell_totals USING (lat_bin, lon_bin)
            ORDER BY focus_counts.record_count DESC
            LIMIT {int(limit)}
            """
        )
        columns = [item[0] for item in result.description]
        rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        if not rows:
            return []

        composition_result = con.execute(
            f"""
            WITH filtered AS ({filtered}),
            focus_cells AS (
                SELECT DISTINCT lat_bin, lon_bin
                FROM filtered
                WHERE {focus_clause}
            ),
            category_counts AS (
                SELECT
                    filtered.lat_bin,
                    filtered.lon_bin,
                    filtered.active_color_value AS color_value,
                    SUM(filtered.record_count) AS record_count
                FROM filtered
                INNER JOIN focus_cells USING (lat_bin, lon_bin)
                GROUP BY filtered.lat_bin, filtered.lon_bin, filtered.active_color_value
            ),
            ranked AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    color_value,
                    record_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY lat_bin, lon_bin
                        ORDER BY record_count DESC, color_value
                    ) AS rank
                FROM category_counts
            )
            SELECT lat_bin, lon_bin, color_value, record_count
            FROM ranked
            WHERE rank <= {int(top_competitors)}
            ORDER BY lat_bin, lon_bin, rank
            """
        ).fetchall()
        composition: dict[tuple[Any, Any], list[str]] = {}
        for lat_bin, lon_bin, color_value, record_count in composition_result:
            composition.setdefault((lat_bin, lon_bin), []).append(
                f"{color_value}: {int(record_count):,}"
            )

        for row in rows:
            row["composition_text"] = "\n".join(
                composition.get((row["lat_bin"], row["lon_bin"]), [])
            )
        return rows
    finally:
        con.close()


def query_all_share_heatmap_bins(
    grid_path: Path,
    filters: SlicerState,
    *,
    limit_per_category: int = 50_000,
    locked_color_dimension: str | None = None,
    coordinate_decimals: int | None = None,
    max_categories: int = 12,
) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        color_by = color_dimension(filters, locked_color_dimension)
        source = coordinate_source_sql(grid_path, coordinate_decimals)
        limit_per_category = max(int(limit_per_category), 1)
        max_categories = max(int(max_categories), 1)
        result = con.execute(
            f"""
            WITH filtered AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    COALESCE(CAST({color_by} AS VARCHAR), 'not supplied') AS active_color_value,
                    record_count
                FROM {source}
                {where_sql(filters)}
            ),
            category_totals AS (
                SELECT
                    active_color_value AS color_value,
                    SUM(record_count) AS category_total_records
                FROM filtered
                GROUP BY active_color_value
                ORDER BY category_total_records DESC, color_value
                LIMIT {max_categories}
            ),
            cell_totals AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    SUM(record_count) AS total_cell_records
                FROM filtered
                GROUP BY lat_bin, lon_bin
            ),
            category_counts AS (
                SELECT
                    filtered.lat_bin,
                    filtered.lon_bin,
                    filtered.active_color_value AS color_value,
                    SUM(filtered.record_count) AS record_count
                FROM filtered
                INNER JOIN category_totals
                    ON filtered.active_color_value = category_totals.color_value
                GROUP BY filtered.lat_bin, filtered.lon_bin, filtered.active_color_value
            ),
            ranked AS (
                SELECT
                    category_counts.lat_bin,
                    category_counts.lon_bin,
                    category_counts.color_value,
                    category_counts.record_count,
                    cell_totals.total_cell_records,
                    category_totals.category_total_records,
                    ROW_NUMBER() OVER (
                        PARTITION BY category_counts.color_value
                        ORDER BY category_counts.record_count DESC,
                                 category_counts.lat_bin,
                                 category_counts.lon_bin
                    ) AS point_rank
                FROM category_counts
                INNER JOIN cell_totals USING (lat_bin, lon_bin)
                INNER JOIN category_totals USING (color_value)
            )
            SELECT
                lat_bin,
                lon_bin,
                record_count,
                total_cell_records,
                category_total_records,
                record_count::DOUBLE / total_cell_records AS share,
                {sql_string(color_by)} AS color_level,
                color_value
            FROM ranked
            WHERE point_rank <= {limit_per_category}
            ORDER BY category_total_records DESC,
                     color_value,
                     record_count DESC,
                     lat_bin,
                     lon_bin
            """
        )
        columns = [item[0] for item in result.description]
        rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        for row in rows:
            row["record_count"] = int(row["record_count"])
            row["total_cell_records"] = int(row["total_cell_records"])
            row["category_total_records"] = int(row["category_total_records"])
            row["share"] = float(row["share"] or 0)
            row["share_percent"] = f"{row['share'] * 100:.1f}%"
            row["composition_text"] = (
                f"{row['color_value']}: {row['record_count']:,} / "
                f"{row['total_cell_records']:,} ({row['share_percent']})"
            )
        return rows
    finally:
        con.close()


def query_composition_markers(
    grid_path: Path,
    filters: SlicerState,
    *,
    limit: int = 250_000,
    locked_color_dimension: str | None = None,
    coordinate_decimals: int | None = None,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        color_by = color_dimension(filters, locked_color_dimension)
        source = coordinate_source_sql(grid_path, coordinate_decimals)
        top_n = max(int(top_n), 1)
        result = con.execute(
            f"""
            WITH filtered AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    COALESCE(CAST({color_by} AS VARCHAR), 'not supplied') AS active_color_value,
                    record_count
                FROM {source}
                {where_sql(filters)}
            ),
            cell_totals AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    SUM(record_count) AS total_record_count
                FROM filtered
                GROUP BY lat_bin, lon_bin
                ORDER BY total_record_count DESC
                LIMIT {int(limit)}
            ),
            category_counts AS (
                SELECT
                    filtered.lat_bin,
                    filtered.lon_bin,
                    filtered.active_color_value AS color_value,
                    SUM(filtered.record_count) AS category_record_count
                FROM filtered
                INNER JOIN cell_totals USING (lat_bin, lon_bin)
                GROUP BY filtered.lat_bin, filtered.lon_bin, filtered.active_color_value
            ),
            ranked AS (
                SELECT
                    category_counts.*,
                    cell_totals.total_record_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY category_counts.lat_bin, category_counts.lon_bin
                        ORDER BY category_record_count DESC, color_value
                    ) AS category_rank
                FROM category_counts
                INNER JOIN cell_totals USING (lat_bin, lon_bin)
            ),
            labeled AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    total_record_count,
                    CASE
                        WHEN category_rank <= {top_n} THEN color_value
                        ELSE 'Other'
                    END AS color_value,
                    CASE
                        WHEN category_rank <= {top_n} THEN category_rank
                        ELSE {top_n + 1}
                    END AS category_order,
                    category_record_count
                FROM ranked
            ),
            collapsed AS (
                SELECT
                    lat_bin,
                    lon_bin,
                    total_record_count,
                    color_value,
                    SUM(category_record_count) AS record_count,
                    MIN(category_order) AS category_order
                FROM labeled
                GROUP BY lat_bin, lon_bin, total_record_count, color_value
            )
            SELECT
                lat_bin,
                lon_bin,
                total_record_count,
                color_value,
                record_count
            FROM collapsed
            ORDER BY total_record_count DESC, lat_bin, lon_bin, category_order, color_value
            """
        )
        marker_rows: dict[tuple[Any, Any], dict[str, Any]] = {}
        for lat_bin, lon_bin, total_record_count, color_value, record_count in result.fetchall():
            key = (lat_bin, lon_bin)
            marker = marker_rows.setdefault(
                key,
                {
                    "lat_bin": lat_bin,
                    "lon_bin": lon_bin,
                    "total_record_count": int(total_record_count),
                    "color_level": color_by,
                    "composition": [],
                    "composition_text": "",
                },
            )
            count = int(record_count)
            total = int(total_record_count)
            share = count / total if total else 0.0
            marker["composition"].append(
                {
                    "value": color_value,
                    "record_count": count,
                    "share": share,
                }
            )

        for marker in marker_rows.values():
            marker["composition_text"] = "\n".join(
                f"{item['value']}: {int(item['record_count']):,} "
                f"({float(item['share']) * 100:.1f}%)"
                for item in marker["composition"]
            )
        return list(marker_rows.values())
    finally:
        con.close()


def mapped_record_count(grid_path: Path, filters: SlicerState) -> int:
    con = duckdb.connect(":memory:")
    try:
        row = con.execute(
            f"""
            SELECT COALESCE(SUM(record_count), 0) AS record_count
            FROM read_parquet({sql_string(Path(grid_path).as_posix())})
            {where_sql(filters)}
            """
        ).fetchone()
        return int(row[0])
    finally:
        con.close()


def option_values(grid_path: Path, filters: SlicerState) -> dict[str, list[Any]]:
    con = duckdb.connect(":memory:")
    try:
        source = f"read_parquet({sql_string(Path(grid_path).as_posix())})"
        options: dict[str, list[Any]] = {}
        for output_key, column in (
            ("families", "family"),
            ("genera", "genus"),
            ("species", "species"),
            ("states", "stateProvince"),
            ("years", "year"),
        ):
            where = where_sql(filters)
            rows = con.execute(
                f"""
                SELECT DISTINCT {column}
                FROM {source}
                {where}
                WHERE {column} IS NOT NULL
                ORDER BY {column}
                """
                if not where
                else f"""
                SELECT DISTINCT {column}
                FROM {source}
                {where} AND {column} IS NOT NULL
                ORDER BY {column}
                """
            ).fetchall()
            options[output_key] = [row[0] for row in rows]
        return options
    finally:
        con.close()


def year_summary(grid_path: Path, filters: SlicerState) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        result = con.execute(
            f"""
            SELECT year, SUM(record_count) AS record_count
            FROM read_parquet({sql_string(Path(grid_path).as_posix())})
            {where_sql(filters)}
            GROUP BY year
            ORDER BY year
            """
        )
        columns = [item[0] for item in result.description]
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    finally:
        con.close()
