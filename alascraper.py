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
  - If no LSID is supplied, q=<scientific_name> is used without an exact
    taxon_name filter by default, because ALA can normalise accepted names.
  - Each species is fetched page-by-page in parallel.
  - Each species gets its own Parquet folder.
  - DuckDB merges all species Parquet files into one final all-species Parquet.
  - CSV output is optional and disabled by default.
"""

from __future__ import annotations

import concurrent.futures as cf
import atexit
import csv
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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
# Replace these with the ALA taxa/species you want to fetch.
# Keep `key` short, lowercase, filesystem-safe, and unique.
# -------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SpeciesTarget:
    key: str
    scientific_name: str
    common_name: str | None = None
    taxon_lsid: str | None = None


@dataclass(frozen=True, slots=True)
class KeywordSpeciesDiscovery:
    keyword_key: str
    query: str
    scientific_name: str
    taxon_lsid: str | None
    common_name: str | None
    taxon_rank: str | None
    family: str | None
    order: str | None


REPO_ROOT = Path(__file__).resolve().parent
GENERATED_SPECIES_TARGETS_PATH = REPO_ROOT / "outputs" / "species_targets_generated.py"
SPECIES_TARGETS_GENERATOR_SCRIPT = REPO_ROOT / "fetch_australian_butterfly_species.py"

# Normal research runs refresh the ALA-backed scientific-name list first, then
# import outputs.species_targets_generated.SPECIES_TARGETS.
REFRESH_SPECIES_TARGETS_BEFORE_RUN = True

# Optional fallback for programmatic/custom runs. The default main workflow uses
# the generated ALA-backed list rather than maintaining a static four-species
# example block in this file.
SPECIES_TARGETS: list[SpeciesTarget] = []

KEYWORD_TARGETS = {
    "general_butterfly": {
        "QUERY": "butterfly",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "butterflies_plural": {
        "QUERY": "butterflies",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "swallowtail": {
        "QUERY": "swallowtail",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "skipper": {
        "QUERY": "skipper",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "blue_butterfly": {
        "QUERY": "blue butterfly",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "monarch": {
        "QUERY": "monarch butterfly",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "copper": {
        "QUERY": "copper butterfly",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "jezebel": {
        "QUERY": "jezebel butterfly",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
    "grass_yellow": {
        "QUERY": "grass yellow butterfly",
        "FACET_FILTERS": [
            'country:"Australia"',
            'order:"Lepidoptera"',
        ],
    },
}

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

# Avoid relevance-score pagination for bulk export. `uuid` is not accepted as an
# ALA sort field, so use eventDate and remove duplicate UUIDs during merge.
ALA_SORT_FIELD = "eventDate"
ALA_SORT_DIRECTION = "asc"
DEDUPE_BY_UUID = True
SEARCH_API_MAX_WINDOW = 5_000
YEAR_FACET_LIMIT = 1_000
KEYWORD_DISCOVERY_PAGE_SIZE = 500
KEYWORD_DISCOVERY_MAX_RECORDS_PER_KEYWORD = SEARCH_API_MAX_WINDOW
KEYWORD_DISCOVERY_FIELDS = [
    "uuid",
    "scientificName",
    "raw_scientificName",
    "species",
    "taxonConceptID",
    "vernacularName",
    "taxonRank",
    "family",
    "order",
]

INVALID_TAXON_LABELS = {
    "not supplied",
    "not provided",
    "not recorded",
    "unknown",
    "unidentified",
    "other values",
}

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
HTTP_POOL_CONNECTIONS = WORKERS * 2
HTTP_POOL_MAXSIZE = WORKERS * 2

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
    "Monash-ALA-species-occurrence-research/0.4 "
    "(contact: replace-with-your-email@monash.edu)"
)

RUN_CONFIG_VERSION = 1

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
    extra_fq_filters: tuple[str, ...] = ()
    partition_label: str = "all"


@dataclass(frozen=True, slots=True)
class PageResult:
    species_key: str
    page_index: int
    start: int
    count: int
    shard_path: Path


@dataclass(frozen=True, slots=True)
class QueryPartition:
    label: str
    extra_fq_filters: tuple[str, ...]
    total_records: int


@dataclass(frozen=True, slots=True)
class SpeciesResult:
    species_key: str
    scientific_name: str
    common_name: str | None
    taxon_lsid: str | None
    config_fingerprint: str
    reported_total_records: int
    pages_written: int
    rows_written: int
    elapsed_seconds: float
    species_parquet_path: Path


_SESSION_LOCAL = threading.local()
_SESSION_REGISTRY_LOCK = threading.Lock()
_SESSION_REGISTRY: list[requests.Session] = []


# =============================================================================
# PATHS
# =============================================================================

def safe_key(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def is_probable_scientific_name(name: str) -> bool:
    text = name.strip()
    parts = text.split()

    if len(parts) < 2:
        return False

    if text.lower() in INVALID_TAXON_LABELS:
        return False

    if parts[0].lower() in {"not", "unknown", "unidentified"}:
        return False

    return bool(re.match(r"^[A-Z][a-zA-Z-]+$", parts[0]))


def coerce_species_target(value: SpeciesTarget | dict[str, Any]) -> SpeciesTarget:
    if isinstance(value, SpeciesTarget):
        return value

    if not isinstance(value, dict):
        raise TypeError(f"Unsupported species target type: {type(value).__name__}")

    try:
        scientific_name = str(value["scientific_name"]).strip()
    except KeyError as exc:
        raise ValueError("Generated species target is missing 'scientific_name'.") from exc

    key = str(value.get("key") or safe_key(scientific_name)).strip()

    if not key or not scientific_name:
        raise ValueError(f"Invalid generated species target: {value!r}")

    return SpeciesTarget(
        key=key,
        scientific_name=scientific_name,
        common_name=value.get("common_name"),
        taxon_lsid=value.get("taxon_lsid"),
    )


def generate_species_targets_file(refresh: bool = REFRESH_SPECIES_TARGETS_BEFORE_RUN) -> None:
    if GENERATED_SPECIES_TARGETS_PATH.exists() and not refresh:
        return

    if not SPECIES_TARGETS_GENERATOR_SCRIPT.exists():
        raise FileNotFoundError(
            f"Missing species target generator script: {SPECIES_TARGETS_GENERATOR_SCRIPT}"
        )

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    module = importlib.import_module("fetch_australian_butterfly_species")

    if not hasattr(module, "generate_species_targets"):
        raise AttributeError(
            "fetch_australian_butterfly_species.py must define generate_species_targets()."
        )

    module.generate_species_targets()


def load_generated_species_targets() -> list[SpeciesTarget]:
    if not GENERATED_SPECIES_TARGETS_PATH.exists():
        raise FileNotFoundError(
            f"Missing generated species targets: {GENERATED_SPECIES_TARGETS_PATH}. "
            "Run fetch_australian_butterfly_species.py first."
        )

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    importlib.invalidate_caches()
    module_name = "outputs.species_targets_generated"

    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    generated_targets = getattr(module, "SPECIES_TARGETS", None)

    if generated_targets is None:
        raise AttributeError(
            "outputs.species_targets_generated must define SPECIES_TARGETS."
        )

    targets = [coerce_species_target(target) for target in generated_targets]

    if not targets:
        raise ValueError("Generated SPECIES_TARGETS is empty.")

    return targets


def resolve_species_targets(
    species_targets: list[SpeciesTarget | dict[str, Any]] | None = None,
    *,
    refresh_generated_targets: bool = REFRESH_SPECIES_TARGETS_BEFORE_RUN,
) -> list[SpeciesTarget]:
    if species_targets is not None:
        targets = [coerce_species_target(target) for target in species_targets]
    elif SPECIES_TARGETS:
        targets = [coerce_species_target(target) for target in SPECIES_TARGETS]
    else:
        generate_species_targets_file(refresh=refresh_generated_targets)
        targets = load_generated_species_targets()

    seen: set[str] = set()
    duplicates: list[str] = []

    for target in targets:
        if target.key in seen:
            duplicates.append(target.key)
        seen.add(target.key)

    if duplicates:
        raise ValueError(f"Duplicate species target keys: {', '.join(sorted(set(duplicates)))}")

    return targets


def species_dir(target: SpeciesTarget) -> Path:
    return SPECIES_ROOT / safe_key(target.key)


def shard_dir(target: SpeciesTarget) -> Path:
    return species_dir(target) / "shards"


def raw_json_dir(target: SpeciesTarget) -> Path:
    return species_dir(target) / "raw_pages"


def species_metadata_path(target: SpeciesTarget) -> Path:
    return species_dir(target) / "run_metadata.json"


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


def format_duration(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{int(hours)}h {int(minutes)}m {remainder:.1f}s"

    if minutes:
        return f"{int(minutes)}m {remainder:.1f}s"

    return f"{remainder:.1f}s"


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


def build_params(
    target: SpeciesTarget,
    start: int,
    page_size: int,
    extra_fq_filters: tuple[str, ...] = (),
) -> list[tuple[str, str | int]]:
    params: list[tuple[str, str | int]] = [
        ("q", build_query(target)),
        ("qualityProfile", QUALITY_PROFILE),
        ("qc", QUALITY_CONTROL),
        (START_PARAM_NAME, start),
        ("pageSize", page_size),
        ("sort", ALA_SORT_FIELD),
        ("dir", ALA_SORT_DIRECTION),
        ("facet", "false"),
    ]

    for fq in [*build_fq_filters(target), *extra_fq_filters]:
        params.append(("fq", fq))

    return params


# =============================================================================
# KEYWORD DISCOVERY
# =============================================================================

def build_keyword_params(
    query: str,
    start: int,
    page_size: int,
    facet_filters: tuple[str, ...],
) -> list[tuple[str, str | int]]:
    params: list[tuple[str, str | int]] = [
        ("q", query),
        ("qualityProfile", QUALITY_PROFILE),
        ("qc", QUALITY_CONTROL),
        (START_PARAM_NAME, start),
        ("pageSize", page_size),
        ("sort", ALA_SORT_FIELD),
        ("dir", ALA_SORT_DIRECTION),
        ("facet", "false"),
        ("fl", ",".join(KEYWORD_DISCOVERY_FIELDS)),
    ]

    for fq in facet_filters:
        params.append(("fq", fq))

    return params


def fetch_keyword_json(
    *,
    keyword_key: str,
    query: str,
    start: int,
    page_size: int,
    facet_filters: tuple[str, ...],
) -> dict[str, Any]:
    params = build_keyword_params(
        query=query,
        start=start,
        page_size=page_size,
        facet_filters=facet_filters,
    )
    last_error: Exception | None = None
    session = get_thread_session()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = min(120, 10 * attempt)
                log(f"HTTP 429 for keyword={keyword_key}, start={start}. Sleeping {wait}s.")
                response.close()
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()

        except Exception as exc:
            last_error = exc
            wait = min(120, 2**attempt)
            log(
                f"Keyword discovery failed: keyword={keyword_key}, start={start}, "
                f"attempt={attempt}/{MAX_RETRIES}, error={exc}. Retrying in {wait}s."
            )
            time.sleep(wait)

    raise RuntimeError(
        f"Failed keyword={keyword_key}, start={start} after {MAX_RETRIES} retries: {last_error}"
    )


def scientific_name_from_discovery_record(record: dict[str, Any]) -> str | None:
    for field in ("scientificName", "raw_scientificName", "species"):
        name = normalise_text_cell(record.get(field))

        if name and is_probable_scientific_name(name):
            return name

    return None


def keyword_records_to_discoveries(
    keyword_key: str,
    query: str,
    records: list[dict[str, Any]],
) -> list[KeywordSpeciesDiscovery]:
    discoveries: list[KeywordSpeciesDiscovery] = []

    for record in records:
        scientific_name = scientific_name_from_discovery_record(record)

        if not scientific_name:
            continue

        discoveries.append(
            KeywordSpeciesDiscovery(
                keyword_key=keyword_key,
                query=query,
                scientific_name=scientific_name,
                taxon_lsid=normalise_text_cell(record.get("taxonConceptID")),
                common_name=normalise_text_cell(record.get("vernacularName")),
                taxon_rank=normalise_text_cell(record.get("taxonRank")),
                family=normalise_text_cell(record.get("family")),
                order=normalise_text_cell(record.get("order")),
            )
        )

    return discoveries


def discover_species_from_keyword_targets(
    keyword_targets: Mapping[str, Mapping[str, Any]] = KEYWORD_TARGETS,
) -> list[KeywordSpeciesDiscovery]:
    all_discoveries: list[KeywordSpeciesDiscovery] = []

    for keyword_key, config in keyword_targets.items():
        query = str(config["QUERY"])
        facet_filters = tuple(str(fq) for fq in config.get("FACET_FILTERS", ()))
        max_records = int(config.get("MAX_RECORDS", KEYWORD_DISCOVERY_MAX_RECORDS_PER_KEYWORD))
        start = 0

        log(
            f"Keyword discovery: key={keyword_key}; query={query!r}; "
            f"max_records={max_records:,}"
        )

        while start < max_records:
            page_size = min(KEYWORD_DISCOVERY_PAGE_SIZE, max_records - start)
            data = fetch_keyword_json(
                keyword_key=keyword_key,
                query=query,
                start=start,
                page_size=page_size,
                facet_filters=facet_filters,
            )
            records = data.get("occurrences", [])

            if not records:
                break

            all_discoveries.extend(
                keyword_records_to_discoveries(keyword_key, query, records)
            )

            total_records = int(data.get("totalRecords", 0) or 0)
            start += len(records)

            if start >= total_records or len(records) < page_size:
                break

    return all_discoveries


def discoveries_to_species_targets(
    discoveries: list[KeywordSpeciesDiscovery],
) -> list[SpeciesTarget]:
    merged: dict[str, KeywordSpeciesDiscovery] = {}

    for discovery in discoveries:
        dedupe_key = discovery.taxon_lsid or normalise_taxon_name(discovery.scientific_name)

        if not dedupe_key:
            continue

        existing = merged.get(dedupe_key)

        if existing is None:
            merged[dedupe_key] = discovery
            continue

        if existing.taxon_lsid is None and discovery.taxon_lsid:
            merged[dedupe_key] = discovery

    targets = [
        SpeciesTarget(
            key=safe_key(discovery.scientific_name),
            scientific_name=discovery.scientific_name,
            common_name=discovery.common_name,
            taxon_lsid=discovery.taxon_lsid,
        )
        for discovery in merged.values()
    ]
    return sorted(targets, key=lambda target: (target.scientific_name, target.taxon_lsid or ""))


def discover_species_targets_from_keywords(
    keyword_targets: Mapping[str, Mapping[str, Any]] = KEYWORD_TARGETS,
) -> list[SpeciesTarget]:
    return discoveries_to_species_targets(
        discover_species_from_keyword_targets(keyword_targets)
    )


# =============================================================================
# RESUME FINGERPRINTING
# =============================================================================

def script_sha256() -> str | None:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None


def schema_signature() -> list[dict[str, str]]:
    return [
        {
            "name": field,
            "dtype": str(SCHEMA[field]),
        }
        for field in FIELDS
    ]


def species_run_config(target: SpeciesTarget) -> dict[str, Any]:
    return {
        "run_config_version": RUN_CONFIG_VERSION,
        "script_sha256": script_sha256(),
        "api_url": API_URL,
        "quality_profile": QUALITY_PROFILE,
        "quality_control": QUALITY_CONTROL,
        "start_param_name": START_PARAM_NAME,
        "country_filter_enabled": COUNTRY_FILTER_ENABLED,
        "country_filter": COUNTRY_FILTER,
        "exact_taxon_name_filter_when_no_lsid": EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID,
        "exact_taxon_name_filter_when_lsid_supplied": EXACT_TAXON_NAME_FILTER_WHEN_LSID_SUPPLIED,
        "ala_sort_field": ALA_SORT_FIELD,
        "ala_sort_direction": ALA_SORT_DIRECTION,
        "dedupe_by_uuid": DEDUPE_BY_UUID,
        "search_api_max_window": SEARCH_API_MAX_WINDOW,
        "year_facet_limit": YEAR_FACET_LIMIT,
        "query": build_query(target),
        "fq_filters": build_fq_filters(target),
        "page_size": PAGE_SIZE,
        "max_records_per_species": MAX_RECORDS_PER_SPECIES,
        "include_user_data_fields": INCLUDE_USER_DATA_FIELDS,
        "write_raw_page_json": WRITE_RAW_PAGE_JSON,
        "fields": FIELDS,
        "schema": schema_signature(),
        "species_target": {
            "key": target.key,
            "scientific_name": target.scientific_name,
            "common_name": target.common_name,
            "taxon_lsid": target.taxon_lsid,
        },
    }


def config_fingerprint(config: dict[str, Any]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_species_metadata(target: SpeciesTarget) -> dict[str, Any] | None:
    path = species_metadata_path(target)

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_species_metadata(target: SpeciesTarget, config: dict[str, Any], fingerprint: str) -> None:
    path = species_metadata_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "created_utc": utc_now(),
                "config_fingerprint": fingerprint,
                "config": config,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def prepare_species_output_for_config(target: SpeciesTarget) -> tuple[dict[str, Any], str]:
    config = species_run_config(target)
    fingerprint = config_fingerprint(config)
    existing_metadata = read_species_metadata(target)
    existing_fingerprint = (
        existing_metadata.get("config_fingerprint")
        if isinstance(existing_metadata, dict)
        else None
    )
    output_dir = species_dir(target)

    if output_dir.exists() and existing_fingerprint != fingerprint:
        reason = "missing" if existing_fingerprint is None else "mismatched"
        log(
            f"Species={target.key}: clearing stale output directory "
            f"because run metadata is {reason}."
        )
        shutil.rmtree(output_dir)

    write_species_metadata(target, config, fingerprint)
    return config, fingerprint


# =============================================================================
# API FETCHING
# =============================================================================

def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    adapter = requests.adapters.HTTPAdapter(
        pool_connections=HTTP_POOL_CONNECTIONS,
        pool_maxsize=HTTP_POOL_MAXSIZE,
        max_retries=0,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_thread_session() -> requests.Session:
    session = getattr(_SESSION_LOCAL, "session", None)

    if isinstance(session, requests.Session):
        return session

    session = create_session()
    _SESSION_LOCAL.session = session

    with _SESSION_REGISTRY_LOCK:
        _SESSION_REGISTRY.append(session)

    return session


def close_all_sessions() -> None:
    with _SESSION_REGISTRY_LOCK:
        sessions = list(_SESSION_REGISTRY)
        _SESSION_REGISTRY.clear()

    for session in sessions:
        session.close()


atexit.register(close_all_sessions)


def fetch_json(
    target: SpeciesTarget,
    start: int,
    page_size: int,
    extra_fq_filters: tuple[str, ...] = (),
) -> dict[str, Any]:
    params = build_params(
        target=target,
        start=start,
        page_size=page_size,
        extra_fq_filters=extra_fq_filters,
    )
    last_error: Exception | None = None
    session = get_thread_session()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = min(120, 10 * attempt)
                log(f"HTTP 429 for species={target.key}, start={start}. Sleeping {wait}s.")
                response.close()
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


def fetch_total_records(
    target: SpeciesTarget,
    extra_fq_filters: tuple[str, ...] = (),
    warn_on_taxon_resolution: bool = True,
) -> int:
    data = fetch_json(target=target, start=0, page_size=1, extra_fq_filters=extra_fq_filters)
    total = int(data.get("totalRecords", 0))

    if warn_on_taxon_resolution:
        warn_if_query_resolves_to_different_taxon(target, data)

    if MAX_RECORDS_PER_SPECIES is not None:
        total = min(total, MAX_RECORDS_PER_SPECIES)

    return total


def fetch_year_facet_partitions(target: SpeciesTarget) -> list[QueryPartition]:
    params: list[tuple[str, str | int]] = [
        ("q", build_query(target)),
        ("qualityProfile", QUALITY_PROFILE),
        ("qc", QUALITY_CONTROL),
        (START_PARAM_NAME, 0),
        ("pageSize", 0),
        ("sort", ALA_SORT_FIELD),
        ("dir", ALA_SORT_DIRECTION),
        ("facet", "true"),
        ("facets", "year"),
        ("flimit", YEAR_FACET_LIMIT),
    ]

    for fq in build_fq_filters(target):
        params.append(("fq", fq))

    response = get_thread_session().get(API_URL, params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    partitions: list[QueryPartition] = []

    for facet in data.get("facetResults", []):
        if facet.get("fieldName") != "year":
            continue

        for result in facet.get("fieldResult", []):
            count = int(result.get("count", 0))
            fq = normalise_text_cell(result.get("fq"))
            label = normalise_text_cell(result.get("label")) or "unknown"

            if count <= 0 or not fq:
                continue

            partitions.append(
                QueryPartition(
                    label=f"year={label}",
                    extra_fq_filters=(fq,),
                    total_records=count,
                )
            )

    return partitions


def make_query_partitions(target: SpeciesTarget, total_records: int) -> list[QueryPartition]:
    if total_records <= SEARCH_API_MAX_WINDOW:
        return [
            QueryPartition(
                label="all",
                extra_fq_filters=(),
                total_records=total_records,
            )
        ]

    partitions = fetch_year_facet_partitions(target)

    if not partitions:
        log(
            f"Species={target.key}: warning: no year facets available; "
            f"search API may return only first {SEARCH_API_MAX_WINDOW:,} rows."
        )
        return [
            QueryPartition(
                label="all",
                extra_fq_filters=(),
                total_records=min(total_records, SEARCH_API_MAX_WINDOW),
            )
        ]

    oversized = [partition for partition in partitions if partition.total_records > SEARCH_API_MAX_WINDOW]

    if oversized:
        labels = ", ".join(f"{p.label} ({p.total_records:,})" for p in oversized)
        raise RuntimeError(
            f"Species={target.key}: year partition(s) exceed search API window: {labels}"
        )

    partition_total = sum(partition.total_records for partition in partitions)
    log(
        f"Species={target.key}: split {total_records:,} records into "
        f"{len(partitions):,} year partitions; partition_total={partition_total:,}"
    )
    return partitions


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

    data = fetch_json(
        target=target,
        start=task.start,
        page_size=task.page_size,
        extra_fq_filters=task.extra_fq_filters,
    )
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


def make_tasks(target: SpeciesTarget, partitions: list[QueryPartition]) -> list[PageTask]:
    tasks: list[PageTask] = []
    page_index = 0

    for partition in partitions:
        total_pages = math.ceil(partition.total_records / PAGE_SIZE)

        for partition_page_index in range(total_pages):
            start = partition_page_index * PAGE_SIZE
            tasks.append(
                PageTask(
                    target=target,
                    page_index=page_index,
                    start=start,
                    page_size=min(PAGE_SIZE, partition.total_records - start),
                    extra_fq_filters=partition.extra_fq_filters,
                    partition_label=partition.label,
                )
            )
            page_index += 1

    return tasks


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

def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def field_select_list(table_alias: str | None = None) -> str:
    prefix = f"{sql_ident(table_alias)}." if table_alias else ""
    return ", ".join(f"{prefix}{sql_ident(field)}" for field in FIELDS)


def deduped_select_sql(source_sql: str) -> str:
    if not DEDUPE_BY_UUID:
        return f"SELECT {field_select_list()} FROM {source_sql}"

    return f"""
        SELECT {field_select_list("deduped")}
        FROM (
            SELECT
                {field_select_list("source")},
                ROW_NUMBER() OVER (
                    PARTITION BY {sql_ident("source")}.{sql_ident("uuid")}
                    ORDER BY
                        {sql_ident("source")}.{sql_ident("eventDate")} NULLS LAST,
                        {sql_ident("source")}.{sql_ident("query_species_key")},
                        {sql_ident("source")}.{sql_ident("uuid")}
                ) AS {sql_ident("_uuid_rank")}
            FROM {source_sql} AS {sql_ident("source")}
        ) AS {sql_ident("deduped")}
        WHERE {sql_ident("deduped")}.{sql_ident("uuid")} IS NULL
           OR {sql_ident("deduped")}.{sql_ident("_uuid_rank")} = 1
    """


def merge_species_shards(target: SpeciesTarget) -> Path:
    species_output = species_parquet_path(target)
    species_output.parent.mkdir(parents=True, exist_ok=True)

    shard_glob = str(shard_dir(target) / "*.parquet").replace("\\", "/")
    output_path = str(species_output).replace("\\", "/")
    source_sql = f"read_parquet({sql_string(shard_glob)})"
    select_sql = deduped_select_sql(source_sql)

    con = duckdb.connect(":memory:")

    try:
        threads = min(WORKERS, os.cpu_count() or WORKERS)
        con.execute(f"PRAGMA threads={threads}")

        species_output.unlink(missing_ok=True)
        input_rows = con.execute(f"SELECT COUNT(*) FROM {source_sql}").fetchone()[0]
        output_rows = con.execute(f"SELECT COUNT(*) FROM ({select_sql})").fetchone()[0]

        if DEDUPE_BY_UUID:
            dropped_rows = input_rows - output_rows
            log(
                f"Species={target.key}: UUID dedupe kept {output_rows:,}/{input_rows:,} "
                f"rows; dropped={dropped_rows:,}"
            )

        con.execute(
            f"""
            COPY (
                {select_sql}
            )
            TO {sql_string(output_path)}
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
        source_sql = f"read_parquet({file_list_sql})"
        select_sql = deduped_select_sql(source_sql)
        input_rows = con.execute(f"SELECT COUNT(*) FROM {source_sql}").fetchone()[0]
        output_rows = con.execute(f"SELECT COUNT(*) FROM ({select_sql})").fetchone()[0]

        con.execute(
            f"""
            CREATE VIEW ala_species_records AS
            {select_sql}
            """
        )

        row_count = con.execute("SELECT COUNT(*) FROM ala_species_records").fetchone()[0]
        log(f"DuckDB sees {row_count:,} all-species rows.")

        if DEDUPE_BY_UUID:
            dropped_rows = input_rows - output_rows
            log(
                f"All-species UUID dedupe kept {output_rows:,}/{input_rows:,} "
                f"rows; dropped={dropped_rows:,}"
            )

        final_parquet = str(FINAL_ALL_SPECIES_PARQUET).replace("\\", "/")
        FINAL_ALL_SPECIES_PARQUET.unlink(missing_ok=True)

        con.execute(
            f"""
            COPY (
                SELECT *
                FROM ala_species_records
            )
            TO {sql_string(final_parquet)}
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
                TO {sql_string(final_csv)}
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

def write_manifest(
    species_results: list[SpeciesResult],
    species_targets: list[SpeciesTarget],
) -> None:
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
                "config_fingerprint",
                "query",
                "fq_filters",
                "reported_total_records",
                "pages_written",
                "rows_written",
                "elapsed_seconds",
                "elapsed_human",
                "species_parquet_path",
            ],
        )
        writer.writeheader()

        target_lookup = {target.key: target for target in species_targets}

        for result in species_results:
            target = target_lookup[result.species_key]
            writer.writerow(
                {
                    "run_utc": utc_now(),
                    "species_key": result.species_key,
                    "scientific_name": result.scientific_name,
                    "common_name": result.common_name or "",
                    "taxon_lsid": result.taxon_lsid or "",
                    "config_fingerprint": result.config_fingerprint,
                    "query": build_query(target),
                    "fq_filters": " | ".join(build_fq_filters(target)),
                    "reported_total_records": result.reported_total_records,
                    "pages_written": result.pages_written,
                    "rows_written": result.rows_written,
                    "elapsed_seconds": f"{result.elapsed_seconds:.3f}",
                    "elapsed_human": format_duration(result.elapsed_seconds),
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
    species_started = time.perf_counter()
    log("=" * 80)
    log(f"Starting species={target.key} | scientific_name={target.scientific_name}")
    _, fingerprint = prepare_species_output_for_config(target)
    log(f"Species={target.key}: config fingerprint={fingerprint}")

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
        elapsed_seconds = time.perf_counter() - species_started
        log(
            f"Species={target.key}: complete in {format_duration(elapsed_seconds)}; "
            "rows_written=0"
        )
        return SpeciesResult(
            species_key=target.key,
            scientific_name=target.scientific_name,
            common_name=target.common_name,
            taxon_lsid=target.taxon_lsid,
            config_fingerprint=fingerprint,
            reported_total_records=0,
            pages_written=0,
            rows_written=0,
            elapsed_seconds=elapsed_seconds,
            species_parquet_path=empty_path,
        )

    partitions = make_query_partitions(target, total_records)
    tasks = make_tasks(target, partitions)
    log(
        f"Species={target.key}: pages to fetch={len(tasks):,}; "
        f"partitions={len(partitions):,}"
    )

    page_results = run_parallel_fetch_for_species(target, tasks)
    rows_written = sum(result.count for result in page_results)

    log(f"Species={target.key}: shard rows written={rows_written:,}")

    merged_path = merge_species_shards(target)
    log(f"Species={target.key}: wrote species Parquet={merged_path}")
    elapsed_seconds = time.perf_counter() - species_started
    log(
        f"Species={target.key}: complete in {format_duration(elapsed_seconds)}; "
        f"rows_written={rows_written:,}; pages_written={len(page_results):,}"
    )

    return SpeciesResult(
        species_key=target.key,
        scientific_name=target.scientific_name,
        common_name=target.common_name,
        taxon_lsid=target.taxon_lsid,
        config_fingerprint=fingerprint,
        reported_total_records=total_records,
        pages_written=len(page_results),
        rows_written=rows_written,
        elapsed_seconds=elapsed_seconds,
        species_parquet_path=merged_path,
    )


def run_alascraper(
    *,
    species_targets: list[SpeciesTarget | dict[str, Any]] | None = None,
    keyword_targets: Mapping[str, Mapping[str, Any]] | None = None,
    write_csv: bool | None = None,
    refresh_generated_targets: bool = REFRESH_SPECIES_TARGETS_BEFORE_RUN,
) -> int:
    global WRITE_CSV

    if write_csv is not None:
        WRITE_CSV = write_csv

    run_started = time.perf_counter()
    validate_privacy_settings()
    prepare_output_dirs()

    if species_targets is not None:
        active_targets = resolve_species_targets(
            species_targets,
            refresh_generated_targets=False,
        )
    elif keyword_targets is not None:
        active_targets = discover_species_targets_from_keywords(keyword_targets)
    else:
        active_targets = resolve_species_targets(
            refresh_generated_targets=refresh_generated_targets,
        )

    log("Starting ALA species-by-species occurrence fetch.")
    log(f"Python version: {sys.version.split()[0]}")
    log(f"Species targets: {len(active_targets):,}")
    log(f"Workers: {WORKERS}")
    log(f"Page size: {PAGE_SIZE}")
    log(f"CSV output enabled: {WRITE_CSV}")
    log(f"User-data fields enabled: {INCLUDE_USER_DATA_FIELDS}")

    species_results: list[SpeciesResult] = []

    for target in active_targets:
        result = fetch_one_species(target)
        species_results.append(result)
        time.sleep(REQUEST_SLEEP_SECONDS_BETWEEN_SPECIES)

    write_manifest(species_results, active_targets)
    merge_all_species(species_results)

    total_elapsed_seconds = time.perf_counter() - run_started
    total_rows = sum(result.rows_written for result in species_results)
    log(
        f"Complete in {format_duration(total_elapsed_seconds)}; "
        f"species={len(species_results):,}; shard_rows_written={total_rows:,}"
    )
    return 0


def main() -> int:
    return run_alascraper()


if __name__ == "__main__":
    raise SystemExit(main())
