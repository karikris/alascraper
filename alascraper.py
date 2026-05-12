#!/usr/bin/env python3.14
"""
Fetch ALA occurrence records species-by-species.

Python: 3.14+

Default output:
  outputs/ala_species_records/
    ├── species/
    │   ├── papilio_aegeus/
    │   │   ├── shards/page_000000.parquet
    │   │   └── papilio_aegeus.parquet
    │   ├── graphium_sarpedon/
    │   │   ├── shards/page_000000.parquet
    │   │   └── graphium_sarpedon.parquet
    │   └── ...
    ├── ala_species_records.parquet
    ├── ala_species_records.duckdb
    ├── species_manifest.csv
    └── run_log.txt

Design:
  - Species are controlled by SPECIES_TARGETS near the top.
  - If a species has an ALA LSID, q=lsid:<LSID> is used.
  - If no LSID is supplied, q=<scientific_name> is used with an exact taxon_name filter.
  - Each species is fetched page-by-page in parallel.
  - Each species gets its own Parquet folder.
  - DuckDB merges all species Parquet files into one final all-species Parquet.
  - CSV output is optional and disabled by default.
"""

from __future__ import annotations

import concurrent.futures as cf
import csv
import json
import math
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import requests


# =============================================================================
# USER-EDITABLE CONSTANTS
# =============================================================================

API_URL = "https://biocache-ws.ala.org.au/ws/occurrences/search"

# -------------------------------------------------------------------------
# Species constants
# -------------------------------------------------------------------------
# Prefer ALA LSIDs where available. Leave taxon_lsid=None to query by
# scientific name.
#
# Replace these with the Australian butterfly species you want to fetch.
# Keep `key` short, lowercase, filesystem-safe, and unique.
# -------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SpeciesTarget:
    key: str
    scientific_name: str
    common_name: str | None = None
    taxon_lsid: str | None = None


SPECIES_TARGETS: list[SpeciesTarget] = [
    SpeciesTarget(
        key="papilio_aegeus",
        scientific_name="Papilio aegeus",
        common_name="Orchard Swallowtail",
        taxon_lsid=None,
    ),
    SpeciesTarget(
        key="graphium_sarpedon",
        scientific_name="Graphium sarpedon",
        common_name="Blue Triangle",
        taxon_lsid=None,
    ),
    SpeciesTarget(
        key="danaus_plexippus",
        scientific_name="Danaus plexippus",
        common_name="Monarch",
        taxon_lsid=None,
    ),
    SpeciesTarget(
        key="pieris_rapae",
        scientific_name="Pieris rapae",
        common_name="Cabbage White",
        taxon_lsid=None,
    ),
]

# -------------------------------------------------------------------------
# Query controls
# -------------------------------------------------------------------------

QUALITY_PROFILE = "ALA"
QUALITY_CONTROL = "-_nest_parent_:*"

# ALA BioCache usually accepts `start` + `pageSize`.
START_PARAM_NAME = "start"

# Add broad fixed filters here.
# Keep COUNTRY_FILTER_ENABLED=True for an Australia-only dataset.
COUNTRY_FILTER_ENABLED = True
COUNTRY_FILTER = 'country:"Australia"'

# ALA can normalise accepted names differently from common field-guide names
# (for example, Papilio aegeus may resolve as Papilio (Princeps) aegeus).
# Keeping this False avoids silently filtering valid records down to zero.
EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID = False

# If True, add fq=taxon_name:"Species name" even when LSID is supplied.
# This can be stricter but may exclude records if ALA normalises names differently.
EXACT_TAXON_NAME_FILTER_WHEN_LSID_SUPPLIED = False

# -------------------------------------------------------------------------
# Performance controls
# -------------------------------------------------------------------------

PAGE_SIZE = 500
WORKERS = 12
MAX_IN_FLIGHT_TASKS = WORKERS * 3

REQUEST_SLEEP_SECONDS_PER_PAGE = 0.05
REQUEST_SLEEP_SECONDS_BETWEEN_SPECIES = 0.5

MAX_RETRIES = 5
TIMEOUT_SECONDS = 90

# Set for testing, e.g. 2_000. Use None for all records per species.
MAX_RECORDS_PER_SPECIES: int | None = None

# -------------------------------------------------------------------------
# Output controls
# -------------------------------------------------------------------------

WRITE_CSV = False
WRITE_DUCKDB_DATABASE = True
WRITE_RAW_PAGE_JSON = False

# Privacy/data-minimisation default:
# keep False unless ethics approval explicitly covers observer names, source
# observation links, and user-submitted media identifiers/URLs.
INCLUDE_USER_DATA_FIELDS = False

# If True, delete all previous outputs.
# If False, existing shard files are reused.
FRESH_RUN = False

OUTPUT_ROOT = Path("outputs") / "ala_species_records"
SPECIES_ROOT = OUTPUT_ROOT / "species"
FINAL_ALL_SPECIES_PARQUET = OUTPUT_ROOT / "ala_species_records.parquet"
FINAL_ALL_SPECIES_CSV = OUTPUT_ROOT / "ala_species_records.csv"
DUCKDB_PATH = OUTPUT_ROOT / "ala_species_records.duckdb"
RUN_LOG_PATH = OUTPUT_ROOT / "run_log.txt"
MANIFEST_PATH = OUTPUT_ROOT / "species_manifest.csv"

PARQUET_COMPRESSION = "zstd"
PARQUET_COMPRESSION_LEVEL = 3
PARQUET_ROW_GROUP_SIZE = 100_000

USER_AGENT = (
    "Monash-Australian-butterfly-occurrence-research/0.3 "
    "(contact: replace-with-your-email@monash.edu)"
)

# -------------------------------------------------------------------------
# Fields retained in the analysis table.
#
# By default, omit fields that can expose observer/user data or link back to
# source observations/media. `uuid` remains as the ALA record identifier.
# -------------------------------------------------------------------------

BASE_FIELDS = [
    "query_species_key",
    "query_scientific_name",
    "query_common_name",
    "query_taxon_lsid",
    "uuid",
    "scientificName",
    "raw_scientificName",
    "vernacularName",
    "taxonRank",
    "taxonConceptID",
    "kingdom",
    "phylum",
    "classs",
    "order",
    "family",
    "genus",
    "species",
    "country",
    "stateProvince",
    "decimalLatitude",
    "decimalLongitude",
    "coordinateUncertaintyInMeters",
    "spatiallyValid",
    "geospatialKosher",
    "eventDate",
    "eventDate_iso",
    "year",
    "month",
    "day",
    "basisOfRecord",
    "raw_basisOfRecord",
    "dataProviderUid",
    "dataProviderName",
    "dataResourceUid",
    "dataResourceName",
    "license",
    "identificationVerificationStatus",
    "recordNumber",
    "assertions",
    "speciesGroups",
    "latLong",
]

USER_DATA_FIELDS = [
    "occurrenceID",
    "recordedBy",
    "collectors",
    "collector",
    "image",
    "images",
    "imageUrl",
    "largeImageUrl",
    "smallImageUrl",
    "thumbnailUrl",
    "imageUrls",
    "references",
    "occurrenceDetails",
]

FIELDS = BASE_FIELDS + (USER_DATA_FIELDS if INCLUDE_USER_DATA_FIELDS else [])

FIELD_SCHEMA: dict[str, pl.DataType] = {
    "query_species_key": pl.Utf8,
    "query_scientific_name": pl.Utf8,
    "query_common_name": pl.Utf8,
    "query_taxon_lsid": pl.Utf8,
    "uuid": pl.Utf8,
    "occurrenceID": pl.Utf8,
    "scientificName": pl.Utf8,
    "raw_scientificName": pl.Utf8,
    "vernacularName": pl.Utf8,
    "taxonRank": pl.Utf8,
    "taxonConceptID": pl.Utf8,
    "kingdom": pl.Utf8,
    "phylum": pl.Utf8,
    "classs": pl.Utf8,
    "order": pl.Utf8,
    "family": pl.Utf8,
    "genus": pl.Utf8,
    "species": pl.Utf8,
    "country": pl.Utf8,
    "stateProvince": pl.Utf8,
    "decimalLatitude": pl.Float64,
    "decimalLongitude": pl.Float64,
    "coordinateUncertaintyInMeters": pl.Float64,
    "spatiallyValid": pl.Boolean,
    "geospatialKosher": pl.Utf8,
    "eventDate": pl.Int64,
    "eventDate_iso": pl.Utf8,
    "year": pl.Int64,
    "month": pl.Utf8,
    "day": pl.Utf8,
    "basisOfRecord": pl.Utf8,
    "raw_basisOfRecord": pl.Utf8,
    "dataProviderUid": pl.Utf8,
    "dataProviderName": pl.Utf8,
    "dataResourceUid": pl.Utf8,
    "dataResourceName": pl.Utf8,
    "recordedBy": pl.Utf8,
    "collectors": pl.Utf8,
    "collector": pl.Utf8,
    "license": pl.Utf8,
    "image": pl.Utf8,
    "images": pl.Utf8,
    "imageUrl": pl.Utf8,
    "largeImageUrl": pl.Utf8,
    "smallImageUrl": pl.Utf8,
    "thumbnailUrl": pl.Utf8,
    "imageUrls": pl.Utf8,
    "references": pl.Utf8,
    "occurrenceDetails": pl.Utf8,
    "identificationVerificationStatus": pl.Utf8,
    "recordNumber": pl.Utf8,
    "assertions": pl.Utf8,
    "speciesGroups": pl.Utf8,
    "latLong": pl.Utf8,
}

SCHEMA: dict[str, pl.DataType] = {field: FIELD_SCHEMA[field] for field in FIELDS}


# =============================================================================
# INTERNAL DATA STRUCTURES
# =============================================================================

@dataclass(frozen=True, slots=True)
class PageTask:
    target: SpeciesTarget
    page_index: int
    start: int
    page_size: int


@dataclass(frozen=True, slots=True)
class PageResult:
    species_key: str
    page_index: int
    start: int
    count: int
    shard_path: Path


@dataclass(frozen=True, slots=True)
class SpeciesResult:
    species_key: str
    scientific_name: str
    common_name: str | None
    taxon_lsid: str | None
    reported_total_records: int
    pages_written: int
    rows_written: int
    species_parquet_path: Path


# =============================================================================
# PATHS
# =============================================================================

def safe_key(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def species_dir(target: SpeciesTarget) -> Path:
    return SPECIES_ROOT / safe_key(target.key)


def shard_dir(target: SpeciesTarget) -> Path:
    return species_dir(target) / "shards"


def raw_json_dir(target: SpeciesTarget) -> Path:
    return species_dir(target) / "raw_pages"


def shard_path(target: SpeciesTarget, page_index: int) -> Path:
    return shard_dir(target) / f"page_{page_index:06d}.parquet"


def raw_json_path(target: SpeciesTarget, page_index: int) -> Path:
    return raw_json_dir(target) / f"page_{page_index:06d}.json"


def species_parquet_path(target: SpeciesTarget) -> Path:
    return species_dir(target) / f"{safe_key(target.key)}.parquet"


# =============================================================================
# LOGGING
# =============================================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    line = f"[{utc_now()}] {message}"
    print(line, flush=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


# =============================================================================
# NORMALISATION
# =============================================================================

def normalise_text_cell(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, list):
        return " | ".join(x for x in (normalise_text_cell(v) for v in value) if x)

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    return str(value)


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in {"true", "t", "1", "yes", "y"}:
        return True

    if text in {"false", "f", "0", "no", "n"}:
        return False

    return None


def epoch_millis_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None

    try:
        millis = int(value)
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)


def normalise_record(target: SpeciesTarget, record: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "query_species_key": target.key,
        "query_scientific_name": target.scientific_name,
        "query_common_name": target.common_name,
        "query_taxon_lsid": target.taxon_lsid,
    }

    for field in FIELDS:
        if field in row:
            continue

        if field == "eventDate_iso":
            row[field] = epoch_millis_to_iso(record.get("eventDate"))

        elif field in {
            "decimalLatitude",
            "decimalLongitude",
            "coordinateUncertaintyInMeters",
        }:
            row[field] = to_float(record.get(field))

        elif field in {"eventDate", "year"}:
            row[field] = to_int(record.get(field))

        elif field == "spatiallyValid":
            row[field] = to_bool(record.get(field))

        else:
            row[field] = normalise_text_cell(record.get(field))

    return row


def normalise_taxon_name(value: Any) -> str | None:
    text = normalise_text_cell(value)

    if not text:
        return None

    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def record_taxon_names(record: dict[str, Any]) -> list[str]:
    names: list[str] = []

    for field in (
        "scientificName",
        "raw_scientificName",
        "species",
        "subspecies",
        "vernacularName",
    ):
        name = normalise_text_cell(record.get(field))

        if name and name not in names:
            names.append(name)

    return names


def record_matches_query_name(target: SpeciesTarget, record: dict[str, Any]) -> bool:
    expected = normalise_taxon_name(target.scientific_name)

    if not expected:
        return False

    for name in record_taxon_names(record):
        candidate = normalise_taxon_name(name)

        if candidate == expected:
            return True

    return False


def warn_if_query_resolves_to_different_taxon(target: SpeciesTarget, data: dict[str, Any]) -> None:
    if target.taxon_lsid:
        return

    records = data.get("occurrences", [])

    if not records:
        return

    first_record = records[0]

    if record_matches_query_name(target, first_record):
        return

    names = ", ".join(record_taxon_names(first_record)) or "unknown"
    log(
        f"Warning: species={target.key} query_name={target.scientific_name!r} "
        f"resolved first record to different ALA taxon/name(s): {names}. "
        "Prefer a taxon_lsid when strict taxonomy is required."
    )


# =============================================================================
# QUERY BUILDING
# =============================================================================

def quote_fq(field: str, value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'{field}:"{escaped}"'


def build_query(target: SpeciesTarget) -> str:
    if target.taxon_lsid:
        return f"lsid:{target.taxon_lsid}"

    return target.scientific_name


def build_fq_filters(target: SpeciesTarget) -> list[str]:
    filters: list[str] = []

    if COUNTRY_FILTER_ENABLED:
        filters.append(COUNTRY_FILTER)

    add_exact_taxon = (
        (target.taxon_lsid is None and EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID)
        or (target.taxon_lsid is not None and EXACT_TAXON_NAME_FILTER_WHEN_LSID_SUPPLIED)
    )

    if add_exact_taxon:
        filters.append(quote_fq("taxon_name", target.scientific_name))

    return filters


def build_params(target: SpeciesTarget, start: int, page_size: int) -> list[tuple[str, str | int]]:
    params: list[tuple[str, str | int]] = [
        ("q", build_query(target)),
        ("qualityProfile", QUALITY_PROFILE),
        ("qc", QUALITY_CONTROL),
        (START_PARAM_NAME, start),
        ("pageSize", page_size),
        ("sort", "score"),
        ("dir", "asc"),
        ("facet", "false"),
    ]

    for fq in build_fq_filters(target):
        params.append(("fq", fq))

    return params


# =============================================================================
# API FETCHING
# =============================================================================

def fetch_json(target: SpeciesTarget, start: int, page_size: int) -> dict[str, Any]:
    params = build_params(target=target, start=start, page_size=page_size)
    last_error: Exception | None = None

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = session.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)

                if response.status_code == 429:
                    wait = min(120, 10 * attempt)
                    log(f"HTTP 429 for species={target.key}, start={start}. Sleeping {wait}s.")
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                return response.json()

            except Exception as exc:
                last_error = exc
                wait = min(120, 2**attempt)
                log(
                    f"Fetch failed: species={target.key}, start={start}, "
                    f"attempt={attempt}/{MAX_RETRIES}, error={exc}. Retrying in {wait}s."
                )
                time.sleep(wait)

    raise RuntimeError(
        f"Failed species={target.key}, start={start} after {MAX_RETRIES} retries: {last_error}"
    )


def fetch_total_records(target: SpeciesTarget) -> int:
    data = fetch_json(target=target, start=0, page_size=1)
    total = int(data.get("totalRecords", 0))
    warn_if_query_resolves_to_different_taxon(target, data)

    if MAX_RECORDS_PER_SPECIES is not None:
        total = min(total, MAX_RECORDS_PER_SPECIES)

    return total


# =============================================================================
# SHARD WRITING
# =============================================================================

def write_page_shard(task: PageTask) -> PageResult:
    target = task.target
    path = shard_path(target, task.page_index)

    if path.exists() and path.stat().st_size > 0:
        try:
            existing_rows = pl.scan_parquet(path).select(pl.len()).collect().item()
            return PageResult(
                species_key=target.key,
                page_index=task.page_index,
                start=task.start,
                count=int(existing_rows),
                shard_path=path,
            )
        except Exception:
            path.unlink(missing_ok=True)

    data = fetch_json(target=target, start=task.start, page_size=task.page_size)
    records = data.get("occurrences", [])

    if WRITE_RAW_PAGE_JSON:
        raw_json_dir(target).mkdir(parents=True, exist_ok=True)
        raw_json_path(target, task.page_index).write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

    rows = [normalise_record(target, record) for record in records]
    df = pl.DataFrame(rows, schema=SCHEMA, orient="row")

    shard_dir(target).mkdir(parents=True, exist_ok=True)
    df.write_parquet(
        path,
        compression=PARQUET_COMPRESSION,
        compression_level=PARQUET_COMPRESSION_LEVEL,
        row_group_size=PARQUET_ROW_GROUP_SIZE,
        statistics=True,
    )

    time.sleep(REQUEST_SLEEP_SECONDS_PER_PAGE)

    return PageResult(
        species_key=target.key,
        page_index=task.page_index,
        start=task.start,
        count=len(rows),
        shard_path=path,
    )


def make_tasks(target: SpeciesTarget, total_records: int) -> list[PageTask]:
    total_pages = math.ceil(total_records / PAGE_SIZE)

    return [
        PageTask(
            target=target,
            page_index=page_index,
            start=page_index * PAGE_SIZE,
            page_size=min(PAGE_SIZE, total_records - page_index * PAGE_SIZE),
        )
        for page_index in range(total_pages)
    ]


def run_parallel_fetch_for_species(target: SpeciesTarget, tasks: list[PageTask]) -> list[PageResult]:
    if not tasks:
        return []

    results: list[PageResult] = []
    pending_tasks = iter(tasks)
    in_flight: dict[cf.Future[PageResult], PageTask] = {}

    def submit_next(executor: cf.ThreadPoolExecutor) -> bool:
        try:
            task = next(pending_tasks)
        except StopIteration:
            return False

        future = executor.submit(write_page_shard, task)
        in_flight[future] = task
        return True

    with cf.ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix=f"ala_{target.key}") as executor:
        for _ in range(min(MAX_IN_FLIGHT_TASKS, len(tasks))):
            submit_next(executor)

        completed = 0
        total = len(tasks)

        while in_flight:
            done, _ = cf.wait(in_flight.keys(), return_when=cf.FIRST_COMPLETED)

            for future in done:
                task = in_flight.pop(future)

                try:
                    result = future.result()
                except Exception as exc:
                    log(
                        f"Page failed permanently: species={target.key}, "
                        f"page={task.page_index}, start={task.start}, error={exc}"
                    )
                    raise

                results.append(result)
                completed += 1

                if completed == 1 or completed % 10 == 0 or completed == total:
                    rows_so_far = sum(r.count for r in results)
                    log(
                        f"Species={target.key}: completed pages={completed:,}/{total:,}; "
                        f"rows_written_so_far={rows_so_far:,}"
                    )

                submit_next(executor)

    return sorted(results, key=lambda r: r.page_index)


# =============================================================================
# DUCKDB FINALISATION
# =============================================================================

def merge_species_shards(target: SpeciesTarget) -> Path:
    species_output = species_parquet_path(target)
    species_output.parent.mkdir(parents=True, exist_ok=True)

    shard_glob = str(shard_dir(target) / "*.parquet").replace("\\", "/")
    output_path = str(species_output).replace("\\", "/")

    con = duckdb.connect(":memory:")

    try:
        threads = min(WORKERS, os.cpu_count() or WORKERS)
        con.execute(f"PRAGMA threads={threads}")

        species_output.unlink(missing_ok=True)

        con.execute(
            f"""
            COPY (
                SELECT *
                FROM read_parquet('{shard_glob}')
            )
            TO '{output_path}'
            (
                FORMAT parquet,
                COMPRESSION '{PARQUET_COMPRESSION}',
                ROW_GROUP_SIZE {PARQUET_ROW_GROUP_SIZE}
            )
            """
        )
    finally:
        con.close()

    return species_output


def merge_all_species(species_results: list[SpeciesResult]) -> None:
    if not species_results:
        log("No species results to merge.")
        return

    species_files = [
        str(result.species_parquet_path).replace("\\", "/")
        for result in species_results
        if result.species_parquet_path.exists()
    ]

    if not species_files:
        log("No species Parquet files found to merge.")
        return

    db_path = str(DUCKDB_PATH) if WRITE_DUCKDB_DATABASE else ":memory:"
    con = duckdb.connect(db_path)

    try:
        threads = min(WORKERS, os.cpu_count() or WORKERS)
        con.execute(f"PRAGMA threads={threads}")

        con.execute("DROP VIEW IF EXISTS ala_species_records")

        file_list_sql = "[" + ",".join("'" + f.replace("'", "''") + "'" for f in species_files) + "]"

        con.execute(
            f"""
            CREATE VIEW ala_species_records AS
            SELECT *
            FROM read_parquet({file_list_sql})
            """
        )

        row_count = con.execute("SELECT COUNT(*) FROM ala_species_records").fetchone()[0]
        log(f"DuckDB sees {row_count:,} all-species rows.")

        final_parquet = str(FINAL_ALL_SPECIES_PARQUET).replace("\\", "/")
        FINAL_ALL_SPECIES_PARQUET.unlink(missing_ok=True)

        con.execute(
            f"""
            COPY (
                SELECT *
                FROM ala_species_records
            )
            TO '{final_parquet}'
            (
                FORMAT parquet,
                COMPRESSION '{PARQUET_COMPRESSION}',
                ROW_GROUP_SIZE {PARQUET_ROW_GROUP_SIZE}
            )
            """
        )
        log(f"Wrote final all-species Parquet: {FINAL_ALL_SPECIES_PARQUET}")

        if WRITE_CSV:
            final_csv = str(FINAL_ALL_SPECIES_CSV).replace("\\", "/")
            FINAL_ALL_SPECIES_CSV.unlink(missing_ok=True)

            con.execute(
                f"""
                COPY (
                    SELECT *
                    FROM ala_species_records
                )
                TO '{final_csv}'
                (
                    HEADER,
                    DELIMITER ','
                )
                """
            )
            log(f"Wrote optional all-species CSV: {FINAL_ALL_SPECIES_CSV}")

    finally:
        con.close()


# =============================================================================
# MANIFEST
# =============================================================================

def write_manifest(species_results: list[SpeciesResult]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    with MANIFEST_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_utc",
                "species_key",
                "scientific_name",
                "common_name",
                "taxon_lsid",
                "query",
                "fq_filters",
                "reported_total_records",
                "pages_written",
                "rows_written",
                "species_parquet_path",
            ],
        )
        writer.writeheader()

        target_lookup = {target.key: target for target in SPECIES_TARGETS}

        for result in species_results:
            target = target_lookup[result.species_key]
            writer.writerow(
                {
                    "run_utc": utc_now(),
                    "species_key": result.species_key,
                    "scientific_name": result.scientific_name,
                    "common_name": result.common_name or "",
                    "taxon_lsid": result.taxon_lsid or "",
                    "query": build_query(target),
                    "fq_filters": " | ".join(build_fq_filters(target)),
                    "reported_total_records": result.reported_total_records,
                    "pages_written": result.pages_written,
                    "rows_written": result.rows_written,
                    "species_parquet_path": str(result.species_parquet_path),
                }
            )

    log(f"Wrote manifest: {MANIFEST_PATH}")


# =============================================================================
# MAIN WORKFLOW
# =============================================================================

def prepare_output_dirs() -> None:
    if FRESH_RUN and OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    SPECIES_ROOT.mkdir(parents=True, exist_ok=True)

    if FRESH_RUN:
        RUN_LOG_PATH.write_text("", encoding="utf-8")


def validate_privacy_settings() -> None:
    if WRITE_RAW_PAGE_JSON and not INCLUDE_USER_DATA_FIELDS:
        raise ValueError(
            "WRITE_RAW_PAGE_JSON=True would store raw observer/source/media fields. "
            "Set INCLUDE_USER_DATA_FIELDS=True only when ethics approval covers it."
        )


def fetch_one_species(target: SpeciesTarget) -> SpeciesResult:
    log("=" * 80)
    log(f"Starting species={target.key} | scientific_name={target.scientific_name}")

    if target.taxon_lsid:
        log(f"Using LSID query: {target.taxon_lsid}")
    else:
        if EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID:
            log("No LSID supplied; using scientific-name query plus exact taxon_name filter.")
        else:
            log("No LSID supplied; using scientific-name query without exact taxon_name filter.")

    total_records = fetch_total_records(target)
    log(f"Species={target.key}: ALA reported total records={total_records:,}")

    if total_records <= 0:
        empty_path = species_parquet_path(target)
        empty_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame([], schema=SCHEMA).write_parquet(empty_path)
        return SpeciesResult(
            species_key=target.key,
            scientific_name=target.scientific_name,
            common_name=target.common_name,
            taxon_lsid=target.taxon_lsid,
            reported_total_records=0,
            pages_written=0,
            rows_written=0,
            species_parquet_path=empty_path,
        )

    tasks = make_tasks(target, total_records)
    log(f"Species={target.key}: pages to fetch={len(tasks):,}")

    page_results = run_parallel_fetch_for_species(target, tasks)
    rows_written = sum(result.count for result in page_results)

    log(f"Species={target.key}: shard rows written={rows_written:,}")

    merged_path = merge_species_shards(target)
    log(f"Species={target.key}: wrote species Parquet={merged_path}")

    return SpeciesResult(
        species_key=target.key,
        scientific_name=target.scientific_name,
        common_name=target.common_name,
        taxon_lsid=target.taxon_lsid,
        reported_total_records=total_records,
        pages_written=len(page_results),
        rows_written=rows_written,
        species_parquet_path=merged_path,
    )


def main() -> int:
    validate_privacy_settings()
    prepare_output_dirs()

    log("Starting ALA species-by-species occurrence fetch.")
    log(f"Python version: {sys.version.split()[0]}")
    log(f"Species targets: {len(SPECIES_TARGETS):,}")
    log(f"Workers: {WORKERS}")
    log(f"Page size: {PAGE_SIZE}")
    log(f"CSV output enabled: {WRITE_CSV}")
    log(f"User-data fields enabled: {INCLUDE_USER_DATA_FIELDS}")

    species_results: list[SpeciesResult] = []

    for target in SPECIES_TARGETS:
        result = fetch_one_species(target)
        species_results.append(result)
        time.sleep(REQUEST_SLEEP_SECONDS_BETWEEN_SPECIES)

    write_manifest(species_results)
    merge_all_species(species_results)

    log("Complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
