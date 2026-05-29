#!/usr/bin/env python3.14
"""Build ABS polygon aggregate Parquet tables for the butterfly dashboard."""

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
DEFAULT_OUTPUT_DIR = Path("datasets/insecta/lepidoptera/dashboard")
DEFAULT_DUCKDB_EXTENSION_DIR = Path("/tmp/duckdb_extensions")
DEFAULT_DISPLAY_SIMPLIFY_TOLERANCE = 0.005
DEFAULT_AREA_LEVEL = "SA2"
SINCE_YEAR_MIN = 1950
ABS_ASGS_BOUNDARY_URL_BASE = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs/"
    "edition-3-july-2021-june-2026/access-and-downloads/"
    "digital-boundary-files"
)
ABS_SA1_GDA2020_SHAPEFILE_URL = (
    f"{ABS_ASGS_BOUNDARY_URL_BASE}/SA1_2021_AUST_SHP_GDA2020.zip"
)
ABS_SA2_GDA2020_SHAPEFILE_URL = (
    f"{ABS_ASGS_BOUNDARY_URL_BASE}/SA2_2021_AUST_SHP_GDA2020.zip"
)
ABS_SA3_GDA2020_SHAPEFILE_URL = (
    f"{ABS_ASGS_BOUNDARY_URL_BASE}/SA3_2021_AUST_SHP_GDA2020.zip"
)
DEFAULT_BOUNDARY_DIR = Path("data/boundaries/asgs_ed3/sa3_2021_gda2020")
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
class AreaLevelConfig:
    level: str
    source_code_column: str
    source_name_column: str
    boundary_dir: Path
    shapefile_name: str
    zip_filename: str
    download_url: str
    bins_filename: str
    boundaries_filename: str
    dimensions_filename: str
    display_simplify_tolerance: float = DEFAULT_DISPLAY_SIMPLIFY_TOLERANCE

    @property
    def output_prefix(self) -> str:
        return self.level.lower()

    @property
    def code_column(self) -> str:
        return f"{self.output_prefix}_code_2021"

    @property
    def name_column(self) -> str:
        return f"{self.output_prefix}_name_2021"

    @property
    def map_geography(self) -> str:
        return f"{self.level}_2021_GDA2020"


AREA_LEVEL_CONFIGS: dict[str, AreaLevelConfig] = {
    "SA1": AreaLevelConfig(
        level="SA1",
        source_code_column="SA1_CODE21",
        source_name_column="SA1_CODE21",
        boundary_dir=Path("data/boundaries/asgs_ed3/sa1_2021_gda2020"),
        shapefile_name="SA1_2021_AUST_GDA2020.shp",
        zip_filename="SA1_2021_AUST_SHP_GDA2020.zip",
        download_url=ABS_SA1_GDA2020_SHAPEFILE_URL,
        bins_filename="butterfly_sa1_bins.parquet",
        boundaries_filename="sa1_boundaries_2021.parquet",
        dimensions_filename="sa1_dimensions.json",
    ),
    "SA2": AreaLevelConfig(
        level="SA2",
        source_code_column="SA2_CODE21",
        source_name_column="SA2_NAME21",
        boundary_dir=Path("data/boundaries/asgs_ed3/sa2_2021_gda2020"),
        shapefile_name="SA2_2021_AUST_GDA2020.shp",
        zip_filename="SA2_2021_AUST_SHP_GDA2020.zip",
        download_url=ABS_SA2_GDA2020_SHAPEFILE_URL,
        bins_filename="butterfly_sa2_bins.parquet",
        boundaries_filename="sa2_boundaries_2021.parquet",
        dimensions_filename="sa2_dimensions.json",
    ),
    "SA3": AreaLevelConfig(
        level="SA3",
        source_code_column="SA3_CODE21",
        source_name_column="SA3_NAME21",
        boundary_dir=DEFAULT_BOUNDARY_DIR,
        shapefile_name="SA3_2021_AUST_GDA2020.shp",
        zip_filename="SA3_2021_AUST_SHP_GDA2020.zip",
        download_url=ABS_SA3_GDA2020_SHAPEFILE_URL,
        bins_filename="butterfly_sa3_bins.parquet",
        boundaries_filename="sa3_boundaries_2021.parquet",
        dimensions_filename="sa3_dimensions.json",
    ),
}


@dataclass(frozen=True)
class AreaBinOutputs:
    area_level: str
    bins: Path
    boundaries: Path
    dimensions_json: Path

    @property
    def sa3_bins(self) -> Path:
        return self.bins

    @property
    def sa3_boundaries(self) -> Path:
        return self.boundaries


SA3BinOutputs = AreaBinOutputs


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def normalise_area_level(area_level: str) -> str:
    level = str(area_level).upper()
    if level not in AREA_LEVEL_CONFIGS:
        valid = ", ".join(AREA_LEVEL_CONFIGS)
        raise ValueError(f"Unknown ABS area level {area_level!r}. Expected one of: {valid}.")
    return level


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


def ensure_area_shapefile(
    config: AreaLevelConfig,
    boundary_dir: Path | None = None,
    *,
    force_download: bool = False,
    download_url: str | None = None,
) -> Path:
    boundary_dir = Path(boundary_dir or config.boundary_dir)
    existing = sorted(boundary_dir.rglob(config.shapefile_name))
    if existing and not force_download:
        return existing[0]

    zip_path = boundary_dir / config.zip_filename
    if force_download or not zip_path.exists():
        download_file(download_url or config.download_url, zip_path)
    safe_extract(zip_path, boundary_dir)

    extracted = sorted(boundary_dir.rglob(config.shapefile_name))
    if not extracted:
        raise FileNotFoundError(
            f"ABS {config.level} shapefile was not found after extraction. "
            f"Expected {config.shapefile_name} inside {boundary_dir}."
        )
    return extracted[0]


def ensure_sa3_shapefile(
    boundary_dir: Path = DEFAULT_BOUNDARY_DIR,
    *,
    download_url: str = ABS_SA3_GDA2020_SHAPEFILE_URL,
    force_download: bool = False,
) -> Path:
    return ensure_area_shapefile(
        AREA_LEVEL_CONFIGS["SA3"],
        Path(boundary_dir),
        force_download=force_download,
        download_url=download_url,
    )


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


def create_area_boundary_table(
    con: duckdb.DuckDBPyConnection,
    shapefile_path: Path,
    config: AreaLevelConfig,
) -> None:
    con.execute(
        f"""
        CREATE TEMP TABLE area_boundaries AS
        SELECT
            CAST({config.source_code_column} AS VARCHAR) AS {config.code_column},
            CAST({config.source_name_column} AS VARCHAR) AS {config.name_column},
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
          AND {config.source_code_column} IS NOT NULL
        """
    )


def create_sa3_boundary_table(con: duckdb.DuckDBPyConnection, shapefile_path: Path) -> None:
    create_area_boundary_table(con, shapefile_path, AREA_LEVEL_CONFIGS["SA3"])


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


def create_matched_table(
    con: duckdb.DuckDBPyConnection,
    config: AreaLevelConfig = AREA_LEVEL_CONFIGS["SA3"],
) -> None:
    con.execute(
        f"""
        CREATE TEMP TABLE matched_observations AS
        SELECT * EXCLUDE (match_rank, geom, min_lon, max_lon, min_lat, max_lat)
        FROM (
            SELECT
                observations.*,
                area_boundaries.{config.code_column},
                area_boundaries.{config.name_column},
                ROW_NUMBER() OVER (
                    PARTITION BY observations.observation_row_id
                    ORDER BY area_boundaries.{config.code_column}
                ) AS match_rank,
                area_boundaries.geom,
                area_boundaries.min_lon,
                area_boundaries.max_lon,
                area_boundaries.min_lat,
                area_boundaries.max_lat
            FROM observations
            INNER JOIN area_boundaries
                ON observations.decimalLongitude BETWEEN area_boundaries.min_lon
                                                AND area_boundaries.max_lon
               AND observations.decimalLatitude BETWEEN area_boundaries.min_lat
                                               AND area_boundaries.max_lat
               AND ST_Covers(
                    area_boundaries.geom,
                    ST_Point(observations.decimalLongitude, observations.decimalLatitude)
               )
        )
        WHERE match_rank = 1
        """
    )


def write_area_outputs(
    con: duckdb.DuckDBPyConnection,
    config: AreaLevelConfig,
    *,
    output_dir: Path,
    display_simplify_tolerance: float,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    boundaries_path = output_dir / config.boundaries_filename
    bins_path = output_dir / config.bins_filename

    con.execute(
        f"""
        COPY (
            WITH occupied_areas AS (
                SELECT DISTINCT {config.code_column}
                FROM matched_observations
            )
            SELECT
                area_boundaries.{config.code_column},
                area_boundaries.{config.name_column},
                area_boundaries.state_code_2021,
                area_boundaries.state_name_2021,
                area_boundaries.area_albers_sqkm,
                ST_AsWKB(
                    ST_SimplifyPreserveTopology(
                        area_boundaries.geom,
                        {float(display_simplify_tolerance)}
                    )
                ) AS geometry_wkb,
                ST_AsGeoJSON(
                    ST_SimplifyPreserveTopology(
                        area_boundaries.geom,
                        {float(display_simplify_tolerance)}
                    )
                ) AS geometry_geojson
            FROM area_boundaries
            INNER JOIN occupied_areas USING ({config.code_column})
            ORDER BY area_boundaries.{config.code_column}
        )
        TO {sql_string(boundaries_path.as_posix())}
        (FORMAT PARQUET)
        """
    )
    con.execute(
        f"""
        COPY (
            SELECT
                {config.code_column},
                {config.name_column},
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
                {config.code_column},
                {config.name_column},
                family,
                genus,
                species,
                scientificName,
                year,
                stateProvince,
                {", ".join(CONSERVATION_COLUMNS)}
            ORDER BY
                {config.code_column},
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


def write_sa3_outputs(
    con: duckdb.DuckDBPyConnection,
    *,
    output_dir: Path,
    display_simplify_tolerance: float,
) -> tuple[Path, Path]:
    return write_area_outputs(
        con,
        AREA_LEVEL_CONFIGS["SA3"],
        output_dir=output_dir,
        display_simplify_tolerance=display_simplify_tolerance,
    )


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
    config: AreaLevelConfig,
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
    boundary_area_count = con.execute("SELECT COUNT(*) FROM area_boundaries").fetchone()[0]
    occupied_area_count = con.execute(
        f"SELECT COUNT(DISTINCT {config.code_column}) FROM matched_observations"
    ).fetchone()[0]
    year_bounds = con.execute(
        "SELECT MIN(year), MAX(year) FROM matched_observations"
    ).fetchone()
    level_key = config.level.lower()
    generic_dimensions = {
        "built_at_utc": utc_timestamp(),
        "source_path": str(source_path),
        "area_level": config.level,
        "abs_area_gda2020_shapefile_url": config.download_url,
        "shapefile_path": str(shapefile_path),
        "bins_path": str(bins_path),
        "boundaries_path": str(boundaries_path),
        "since_year_min": since_year,
        "display_simplify_tolerance_degrees": display_simplify_tolerance,
        "source_row_count": int(source_counts[0]),
        "since_1950_row_count": int(source_counts[1]),
        "coordinate_row_count": int(source_counts[2]),
        "matched_record_count": int(matched_count),
        "unmatched_coordinate_count": int(source_counts[2]) - int(matched_count),
        "boundary_area_count": int(boundary_area_count),
        "occupied_area_count": int(occupied_area_count),
        "area_count": int(occupied_area_count),
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
        "map_geography": config.map_geography,
    }
    generic_dimensions.update(
        {
            f"abs_{level_key}_gda2020_shapefile_url": config.download_url,
            f"{level_key}_shapefile_path": str(shapefile_path),
            f"{level_key}_bins_path": str(bins_path),
            f"{level_key}_boundaries_path": str(boundaries_path),
            f"{level_key}_matched_record_count": int(matched_count),
            f"{level_key}_unmatched_coordinate_count": int(source_counts[2])
            - int(matched_count),
            f"{level_key}_boundary_area_count": int(boundary_area_count),
            f"{level_key}_area_count": int(occupied_area_count),
        }
    )
    return generic_dimensions


def build_area_bins(
    *,
    area_level: str = DEFAULT_AREA_LEVEL,
    source_path: Path = DEFAULT_SOURCE_PATH,
    boundary_dir: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    since_year: int = SINCE_YEAR_MIN,
    force_download: bool = False,
    duckdb_extension_dir: Path = DEFAULT_DUCKDB_EXTENSION_DIR,
    display_simplify_tolerance: float | None = None,
) -> AreaBinOutputs:
    level = normalise_area_level(area_level)
    config = AREA_LEVEL_CONFIGS[level]
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    simplify_tolerance = (
        config.display_simplify_tolerance
        if display_simplify_tolerance is None
        else float(display_simplify_tolerance)
    )
    shapefile_path = ensure_area_shapefile(
        config,
        Path(boundary_dir) if boundary_dir is not None else None,
        force_download=force_download,
    )
    con = connect_spatial(duckdb_extension_dir)
    try:
        create_area_boundary_table(con, shapefile_path, config)
        create_observation_table(con, source_path, since_year=since_year)
        create_matched_table(con, config)
        bins_path, boundaries_path = write_area_outputs(
            con,
            config,
            output_dir=output_dir,
            display_simplify_tolerance=simplify_tolerance,
        )
        dimensions_path = output_dir / config.dimensions_filename
        dimensions = build_dimensions(
            con,
            config=config,
            source_path=source_path,
            shapefile_path=shapefile_path,
            bins_path=bins_path,
            boundaries_path=boundaries_path,
            since_year=since_year,
            display_simplify_tolerance=simplify_tolerance,
        )
        dimensions_path.write_text(
            json.dumps(dimensions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        con.close()

    return AreaBinOutputs(
        area_level=config.level,
        bins=bins_path,
        boundaries=boundaries_path,
        dimensions_json=dimensions_path,
    )


def build_sa3_bins(
    *,
    source_path: Path = DEFAULT_SOURCE_PATH,
    boundary_dir: Path = DEFAULT_BOUNDARY_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    since_year: int = SINCE_YEAR_MIN,
    force_download: bool = False,
    duckdb_extension_dir: Path = DEFAULT_DUCKDB_EXTENSION_DIR,
    display_simplify_tolerance: float = DEFAULT_DISPLAY_SIMPLIFY_TOLERANCE,
) -> AreaBinOutputs:
    return build_area_bins(
        area_level="SA3",
        source_path=source_path,
        boundary_dir=boundary_dir,
        output_dir=output_dir,
        since_year=since_year,
        force_download=force_download,
        duckdb_extension_dir=duckdb_extension_dir,
        display_simplify_tolerance=display_simplify_tolerance,
    )


def build_all_area_bins(
    *,
    source_path: Path = DEFAULT_SOURCE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    since_year: int = SINCE_YEAR_MIN,
    force_download: bool = False,
    duckdb_extension_dir: Path = DEFAULT_DUCKDB_EXTENSION_DIR,
    display_simplify_tolerance: float | None = None,
) -> list[AreaBinOutputs]:
    return [
        build_area_bins(
            area_level=area_level,
            source_path=source_path,
            output_dir=output_dir,
            since_year=since_year,
            force_download=force_download,
            duckdb_extension_dir=duckdb_extension_dir,
            display_simplify_tolerance=display_simplify_tolerance,
        )
        for area_level in AREA_LEVEL_CONFIGS
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build ABS polygon aggregate bins for dashboard maps."
    )
    parser.add_argument(
        "--area-level",
        choices=[*AREA_LEVEL_CONFIGS, "all"],
        default=DEFAULT_AREA_LEVEL,
        help="ABS geography to build. Use 'all' to build SA1, SA2, and SA3.",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument(
        "--boundary-dir",
        type=Path,
        default=None,
        help="Override boundary directory for one selected area level.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--since-year", type=int, default=SINCE_YEAR_MIN)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--duckdb-extension-dir", type=Path, default=DEFAULT_DUCKDB_EXTENSION_DIR)
    parser.add_argument(
        "--display-simplify-tolerance",
        type=float,
        default=None,
        help=(
            "Douglas-Peucker topology-preserving simplification tolerance in degrees "
            "for display geometry written to Parquet. Full ABS geometry is still used "
            "for the spatial join."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.area_level == "all":
        if args.boundary_dir is not None:
            raise SystemExit("--boundary-dir can only be used with one --area-level value.")
        outputs = build_all_area_bins(
            source_path=args.source,
            output_dir=args.output_dir,
            since_year=args.since_year,
            force_download=args.force_download,
            duckdb_extension_dir=args.duckdb_extension_dir,
            display_simplify_tolerance=args.display_simplify_tolerance,
        )
    else:
        outputs = [
            build_area_bins(
                area_level=args.area_level,
                source_path=args.source,
                boundary_dir=args.boundary_dir,
                output_dir=args.output_dir,
                since_year=args.since_year,
                force_download=args.force_download,
                duckdb_extension_dir=args.duckdb_extension_dir,
                display_simplify_tolerance=args.display_simplify_tolerance,
            )
        ]

    for output in outputs:
        print(f"Wrote {output.area_level} bins: {output.bins}")
        print(f"Wrote {output.area_level} boundaries: {output.boundaries}")
        print(f"Wrote {output.area_level} dimensions: {output.dimensions_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
