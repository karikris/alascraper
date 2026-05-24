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


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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


def query_grid_bins(
    grid_path: Path,
    filters: SlicerState,
    *,
    limit: int = 250_000,
    locked_color_dimension: str | None = None,
) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        color_by = color_dimension(filters, locked_color_dimension)
        group_columns = GROUP_COLUMNS_BY_COLOR_DIMENSION[color_by]
        group_sql = ", ".join(group_columns)
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
            FROM read_parquet({sql_string(Path(grid_path).as_posix())})
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
