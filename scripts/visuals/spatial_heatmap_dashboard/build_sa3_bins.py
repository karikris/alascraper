#!/usr/bin/env python3.14
"""Build SA3 polygon aggregate Parquet tables for the butterfly dashboard."""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb


DEFAULT_SOURCE_PATH = Path("datasets/insecta/lepidoptera/butterflies_conservation.parquet")
DEFAULT_BOUNDARY_DIR = Path("data/boundaries/asgs_ed3/sa3_2021_gda2020")
DEFAULT_OUTPUT_DIR = Path("datasets/insecta/lepidoptera/dashboard")
DEFAULT_DUCKDB_EXTENSION_DIR = Path("/tmp/duckdb_extensions")
DEFAULT_DISPLAY_SIMPLIFY_TOLERANCE = 0.005
SINCE_YEAR_MIN = 1950
ABS_SA3_GDA2020_SHAPEFILE_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs/"
    "edition-3-july-2021-june-2026/access-and-downloads/"
    "digital-boundary-files/SA3_2021_AUST_SHP_GDA2020.zip"
)
CONSERVATION_COLUMNS = [
    "Status",
    "state_status",
    "state_status_level",
    "state_status_for_occurrence",
    "state_status_jurisdiction_matched",
    "state_status_qualifier",
    "epbc_listed_taxon",
    "state_listed_taxon",
    "epbc_sprat_url",
    "epbc_conservation_advice_url",
    "epbc_recovery_plan_url",
    "epbc_protected_matters_url",
]


@dataclass(frozen=True)
class SA3BinOutputs:
    sa3_bins: Path
    sa3_boundaries: Path
    dimensions_json: Path


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def safe_extract(zip_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            destination = (output_dir / member.filename).resolve()
            if not str(destination).startswith(str(output_dir.resolve())):
                raise ValueError(f"Refusing to extract unsafe zip member: {member.filename}")
        archive.extractall(output_dir)


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(url) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    partial.replace(destination)


def ensure_sa3_shapefile(
    boundary_dir: Path = DEFAULT_BOUNDARY_DIR,
    *,
    download_url: str = ABS_SA3_GDA2020_SHAPEFILE_URL,
    force_download: bool = False,
) -> Path:
    boundary_dir = Path(boundary_dir)
    existing = sorted(boundary_dir.rglob("SA3_2021_AUST_GDA2020.shp"))
    if existing and not force_download:
        return existing[0]

    zip_path = boundary_dir / "SA3_2021_AUST_SHP_GDA2020.zip"
    if force_download or not zip_path.exists():
        download_file(download_url, zip_path)
    safe_extract(zip_path, boundary_dir)

    extracted = sorted(boundary_dir.rglob("SA3_2021_AUST_GDA2020.shp"))
    if not extracted:
        raise FileNotFoundError(
            "ABS SA3 shapefile was not found after extraction. Expected "
            "SA3_2021_AUST_GDA2020.shp inside "
            f"{boundary_dir}."
        )
    return extracted[0]


def connect_spatial(extension_dir: Path = DEFAULT_DUCKDB_EXTENSION_DIR) -> duckdb.DuckDBPyConnection:
    extension_dir = Path(extension_dir)
    extension_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute(f"SET extension_directory = {sql_string(extension_dir)}")
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    return con


def parquet_columns(con: duckdb.DuckDBPyConnection, source_path: Path) -> set[str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet({sql_string(source_path.as_posix())})"
    ).fetchall()
    return {str(row[0]) for row in rows}


def source_select_columns(columns: set[str]) -> list[str]:
    required_columns = [
        "uuid",
        "family",
        "genus",
        "species",
        "scientificName",
        "taxonConceptID",
        "stateProvince",
        "year",
        "decimalLatitude",
        "decimalLongitude",
    ]
    selected = [
        column if column in columns else f"NULL AS {column}"
        for column in required_columns
    ]
    selected.extend(
        column if column in columns else f"NULL AS {column}"
        for column in CONSERVATION_COLUMNS
    )
    return selected


def create_sa3_boundary_table(con: duckdb.DuckDBPyConnection, shapefile_path: Path) -> None:
    con.execute(
        f"""
        CREATE TEMP TABLE sa3_boundaries AS
        SELECT
            CAST(SA3_CODE21 AS VARCHAR) AS sa3_code_2021,
            CAST(SA3_NAME21 AS VARCHAR) AS sa3_name_2021,
            CAST(STE_CODE21 AS VARCHAR) AS state_code_2021,
            CAST(STE_NAME21 AS VARCHAR) AS state_name_2021,
            CAST(AREASQKM21 AS DOUBLE) AS area_albers_sqkm,
            geom,
            ST_XMin(geom) AS min_lon,
            ST_XMax(geom) AS max_lon,
            ST_YMin(geom) AS min_lat,
            ST_YMax(geom) AS max_lat
        FROM ST_Read({sql_string(shapefile_path.as_posix())})
        WHERE geom IS NOT NULL
          AND SA3_CODE21 IS NOT NULL
        """
    )


def create_observation_table(
    con: duckdb.DuckDBPyConnection,
    source_path: Path,
    *,
    since_year: int,
) -> None:
    columns = parquet_columns(con, source_path)
    selected = source_select_columns(columns)
    con.execute(
        f"""
        CREATE TEMP TABLE observations AS
        SELECT
            ROW_NUMBER() OVER () AS observation_row_id,
            {", ".join(selected)}
        FROM read_parquet({sql_string(source_path.as_posix())})
        WHERE decimalLatitude IS NOT NULL
          AND decimalLongitude IS NOT NULL
          AND year >= {int(since_year)}
        """
    )


def create_matched_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TEMP TABLE matched_observations AS
        SELECT * EXCLUDE (match_rank, geom, min_lon, max_lon, min_lat, max_lat)
        FROM (
            SELECT
                observations.*,
                sa3_boundaries.sa3_code_2021,
                sa3_boundaries.sa3_name_2021,
                ROW_NUMBER() OVER (
                    PARTITION BY observations.observation_row_id
                    ORDER BY sa3_boundaries.sa3_code_2021
                ) AS match_rank,
                sa3_boundaries.geom,
                sa3_boundaries.min_lon,
                sa3_boundaries.max_lon,
                sa3_boundaries.min_lat,
                sa3_boundaries.max_lat
            FROM observations
            INNER JOIN sa3_boundaries
                ON observations.decimalLongitude BETWEEN sa3_boundaries.min_lon
                                                AND sa3_boundaries.max_lon
               AND observations.decimalLatitude BETWEEN sa3_boundaries.min_lat
                                               AND sa3_boundaries.max_lat
               AND ST_Covers(
                    sa3_boundaries.geom,
                    ST_Point(observations.decimalLongitude, observations.decimalLatitude)
               )
        )
        WHERE match_rank = 1
        """
    )


def write_sa3_outputs(
    con: duckdb.DuckDBPyConnection,
    *,
    output_dir: Path,
    display_simplify_tolerance: float,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    boundaries_path = output_dir / "sa3_boundaries_2021.parquet"
    bins_path = output_dir / "butterfly_sa3_bins.parquet"

    con.execute(
        f"""
        COPY (
            SELECT
                sa3_code_2021,
                sa3_name_2021,
                state_code_2021,
                state_name_2021,
                area_albers_sqkm,
                ST_AsWKB(
                    ST_SimplifyPreserveTopology(
                        geom,
                        {float(display_simplify_tolerance)}
                    )
                ) AS geometry_wkb,
                ST_AsGeoJSON(
                    ST_SimplifyPreserveTopology(
                        geom,
                        {float(display_simplify_tolerance)}
                    )
                ) AS geometry_geojson
            FROM sa3_boundaries
            ORDER BY sa3_code_2021
        )
        TO {sql_string(boundaries_path.as_posix())}
        (FORMAT PARQUET)
        """
    )
    con.execute(
        f"""
        COPY (
            SELECT
                sa3_code_2021,
                sa3_name_2021,
                family,
                genus,
                species,
                scientificName,
                year,
                stateProvince,
                {", ".join(CONSERVATION_COLUMNS)},
                COUNT(*) AS record_count,
                COUNT(DISTINCT scientificName) AS distinct_scientific_names,
                COUNT(DISTINCT taxonConceptID) AS distinct_taxon_concepts,
                MIN(year) AS min_year,
                MAX(year) AS max_year
            FROM matched_observations
            GROUP BY
                sa3_code_2021,
                sa3_name_2021,
                family,
                genus,
                species,
                scientificName,
                year,
                stateProvince,
                {", ".join(CONSERVATION_COLUMNS)}
            ORDER BY
                sa3_code_2021,
                family,
                genus,
                species,
                scientificName,
                year,
                stateProvince
        )
        TO {sql_string(bins_path.as_posix())}
        (FORMAT PARQUET)
        """
    )
    return bins_path, boundaries_path


def distinct_values(
    con: duckdb.DuckDBPyConnection,
    *,
    table: str,
    column: str,
) -> list[Any]:
    return [
        row[0]
        for row in con.execute(
            f"""
            SELECT DISTINCT {column}
            FROM {table}
            WHERE {column} IS NOT NULL
            ORDER BY {column}
            """
        ).fetchall()
    ]


def build_dimensions(
    con: duckdb.DuckDBPyConnection,
    *,
    source_path: Path,
    shapefile_path: Path,
    bins_path: Path,
    boundaries_path: Path,
    since_year: int,
    display_simplify_tolerance: float,
) -> dict[str, Any]:
    source_counts = con.execute(
        f"""
        SELECT
            COUNT(*) AS source_row_count,
            COUNT(*) FILTER (WHERE year >= {int(since_year)}) AS since_1950_row_count,
            COUNT(*) FILTER (
                WHERE year >= {int(since_year)}
                  AND decimalLatitude IS NOT NULL
                  AND decimalLongitude IS NOT NULL
            ) AS coordinate_row_count
        FROM read_parquet({sql_string(source_path.as_posix())})
        """
    ).fetchone()
    matched_count = con.execute("SELECT COUNT(*) FROM matched_observations").fetchone()[0]
    area_count = con.execute("SELECT COUNT(*) FROM sa3_boundaries").fetchone()[0]
    year_bounds = con.execute(
        "SELECT MIN(year), MAX(year) FROM matched_observations"
    ).fetchone()
    return {
        "built_at_utc": utc_timestamp(),
        "source_path": str(source_path),
        "abs_sa3_gda2020_shapefile_url": ABS_SA3_GDA2020_SHAPEFILE_URL,
        "sa3_shapefile_path": str(shapefile_path),
        "sa3_bins_path": str(bins_path),
        "sa3_boundaries_path": str(boundaries_path),
        "since_year_min": since_year,
        "display_simplify_tolerance_degrees": display_simplify_tolerance,
        "source_row_count": int(source_counts[0]),
        "since_1950_row_count": int(source_counts[1]),
        "coordinate_row_count": int(source_counts[2]),
        "sa3_matched_record_count": int(matched_count),
        "sa3_unmatched_coordinate_count": int(source_counts[2]) - int(matched_count),
        "sa3_area_count": int(area_count),
        "family_values": distinct_values(con, table="matched_observations", column="family"),
        "genus_values": distinct_values(con, table="matched_observations", column="genus"),
        "species_values": distinct_values(con, table="matched_observations", column="species"),
        "scientific_name_values": distinct_values(
            con,
            table="matched_observations",
            column="scientificName",
        ),
        "state_values": distinct_values(
            con,
            table="matched_observations",
            column="stateProvince",
        ),
        "year_values": distinct_values(con, table="matched_observations", column="year"),
        "status_values": distinct_values(con, table="matched_observations", column="Status"),
        "state_status_level_values": distinct_values(
            con,
            table="matched_observations",
            column="state_status_level",
        ),
        "min_year": year_bounds[0],
        "max_year": year_bounds[1],
        "canonical_species_column": "species",
        "map_geography": "SA3_2021_GDA2020",
    }


def build_sa3_bins(
    *,
    source_path: Path = DEFAULT_SOURCE_PATH,
    boundary_dir: Path = DEFAULT_BOUNDARY_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    since_year: int = SINCE_YEAR_MIN,
    force_download: bool = False,
    duckdb_extension_dir: Path = DEFAULT_DUCKDB_EXTENSION_DIR,
    display_simplify_tolerance: float = DEFAULT_DISPLAY_SIMPLIFY_TOLERANCE,
) -> SA3BinOutputs:
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    shapefile_path = ensure_sa3_shapefile(
        Path(boundary_dir),
        force_download=force_download,
    )
    con = connect_spatial(duckdb_extension_dir)
    try:
        create_sa3_boundary_table(con, shapefile_path)
        create_observation_table(con, source_path, since_year=since_year)
        create_matched_table(con)
        bins_path, boundaries_path = write_sa3_outputs(
            con,
            output_dir=output_dir,
            display_simplify_tolerance=display_simplify_tolerance,
        )
        dimensions_path = output_dir / "sa3_dimensions.json"
        dimensions = build_dimensions(
            con,
            source_path=source_path,
            shapefile_path=shapefile_path,
            bins_path=bins_path,
            boundaries_path=boundaries_path,
            since_year=since_year,
            display_simplify_tolerance=display_simplify_tolerance,
        )
        dimensions_path.write_text(
            json.dumps(dimensions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        con.close()

    return SA3BinOutputs(
        sa3_bins=bins_path,
        sa3_boundaries=boundaries_path,
        dimensions_json=dimensions_path,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build ABS SA3 polygon aggregate bins for dashboard maps."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--boundary-dir", type=Path, default=DEFAULT_BOUNDARY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--since-year", type=int, default=SINCE_YEAR_MIN)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--duckdb-extension-dir", type=Path, default=DEFAULT_DUCKDB_EXTENSION_DIR)
    parser.add_argument(
        "--display-simplify-tolerance",
        type=float,
        default=DEFAULT_DISPLAY_SIMPLIFY_TOLERANCE,
        help=(
            "Douglas-Peucker topology-preserving simplification tolerance in degrees "
            "for display geometry written to Parquet. Full ABS geometry is still used "
            "for the spatial join."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = build_sa3_bins(
        source_path=args.source,
        boundary_dir=args.boundary_dir,
        output_dir=args.output_dir,
        since_year=args.since_year,
        force_download=args.force_download,
        duckdb_extension_dir=args.duckdb_extension_dir,
        display_simplify_tolerance=args.display_simplify_tolerance,
    )
    print(f"Wrote SA3 bins: {outputs.sa3_bins}")
    print(f"Wrote SA3 boundaries: {outputs.sa3_boundaries}")
    print(f"Wrote SA3 dimensions: {outputs.dimensions_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
