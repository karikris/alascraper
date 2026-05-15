#!/usr/bin/env python3.14
"""
Fetch ALA occurrence records species-by-species.

Python: 3.14+

Default output:
  datasets/misc/lepidoptera/
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

import argparse
import atexit
import concurrent.futures as cf
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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import requests

from constants import (
    ALA_SORT_DIRECTION,
    ALA_SORT_FIELD,
    API_URL,
    COUNTRY_FILTER,
    COUNTRY_FILTER_ENABLED,
    DATASETS_ROOT,
    DEDUPE_BY_UUID,
    DEFAULT_GENERATED_ORDER,
    EXACT_TAXON_NAME_FILTER_WHEN_LSID_SUPPLIED,
    EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID,
    FIELDS,
    FRESH_RUN,
    HTTP_POOL_CONNECTIONS,
    HTTP_POOL_MAXSIZE,
    INCLUDE_USER_DATA_FIELDS,
    INVALID_TAXON_LABELS,
    MAX_IN_FLIGHT_TASKS,
    MAX_RECORDS_PER_SPECIES,
    MAX_RETRIES,
    PAGE_SIZE,
    PARQUET_COMPRESSION,
    PARQUET_COMPRESSION_LEVEL,
    PARQUET_ROW_GROUP_SIZE,
    QUALITY_CONTROL,
    QUALITY_PROFILE,
    REFRESH_SPECIES_TARGETS_BEFORE_RUN,
    REQUEST_SLEEP_SECONDS_BETWEEN_SPECIES,
    REQUEST_SLEEP_SECONDS_PER_PAGE,
    RESUME_CACHE_VERSION,
    RUN_CONFIG_VERSION,
    SCHEMA,
    SEARCH_API_MAX_WINDOW,
    START_PARAM_NAME,
    TIMEOUT_SECONDS,
    USER_AGENT,
    WORKERS,
    WRITE_CSV,
    WRITE_DUCKDB_DATABASE,
    WRITE_RAW_PAGE_JSON,
    YEAR_FACET_LIMIT,
)


# =============================================================================
# USER TARGETS
# =============================================================================

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
    source_order: str | None = None
    facet_fq_filters: tuple[str, ...] = ()
    target_match_suspect: bool = False


REPO_ROOT = Path(__file__).resolve().parent
SPECIES_TARGETS_GENERATOR_SCRIPT = REPO_ROOT / "scripts" / "fetch_by_order.py"
SPECIES_TARGETS_GENERATOR_MODULE = "scripts.fetch_by_order"

# Optional fallback for programmatic/custom runs. The default main workflow uses
# the generated ALA-backed list rather than maintaining a static four-species
# example block in this file.
SPECIES_TARGETS: list[SpeciesTarget] = []

OUTPUT_ROOT = DATASETS_ROOT / "misc" / "lepidoptera"
SPECIES_ROOT = OUTPUT_ROOT / "species"
FINAL_ALL_SPECIES_PARQUET = OUTPUT_ROOT / "ala_species_records.parquet"
FINAL_ALL_SPECIES_CSV = OUTPUT_ROOT / "ala_species_records.csv"
DUCKDB_PATH = OUTPUT_ROOT / "ala_species_records.duckdb"
RUN_LOG_PATH = OUTPUT_ROOT / "run_log.txt"
MANIFEST_PATH = OUTPUT_ROOT / "species_manifest.csv"


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
    validation_status: str = "complete"
    validation_detail: str | None = None


@dataclass(frozen=True, slots=True)
class QueryPartition:
    label: str
    extra_fq_filters: tuple[str, ...]
    total_records: int


@dataclass(frozen=True, slots=True)
class QueryPartitionPlan:
    partitions: list[QueryPartition]
    coverage_status: str
    coverage_detail: str | None
    expected_total_records: int
    planned_partition_records: int


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
    target_match_suspect: bool = False
    partition_coverage_status: str = "complete"
    partition_coverage_detail: str | None = None
    planned_partition_records: int = 0
    page_validation_issue_count: int = 0
    page_validation_detail: str | None = None
    fetch_status: str = "complete"
    fetch_error: str | None = None


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


def dataset_output_root(order: str | None, dataset_class: str | None) -> Path:
    class_key = safe_key(dataset_class or "") or "misc"
    order_key = safe_key(order or DEFAULT_GENERATED_ORDER)

    if not order_key:
        raise ValueError("Order must not be empty.")

    return DATASETS_ROOT / class_key / order_key


def generated_species_targets_path(order: str | None = None) -> Path:
    order_key = safe_key(order or DEFAULT_GENERATED_ORDER)

    if not order_key:
        raise ValueError("Order must not be empty.")

    return OUTPUT_ROOT / f"{order_key}_species.json"


def configure_output_root(output_root: Path) -> None:
    global OUTPUT_ROOT
    global SPECIES_ROOT
    global FINAL_ALL_SPECIES_PARQUET
    global FINAL_ALL_SPECIES_CSV
    global DUCKDB_PATH
    global RUN_LOG_PATH
    global MANIFEST_PATH
    OUTPUT_ROOT = output_root
    SPECIES_ROOT = OUTPUT_ROOT / "species"
    FINAL_ALL_SPECIES_PARQUET = OUTPUT_ROOT / "ala_species_records.parquet"
    FINAL_ALL_SPECIES_CSV = OUTPUT_ROOT / "ala_species_records.csv"
    DUCKDB_PATH = OUTPUT_ROOT / "ala_species_records.duckdb"
    RUN_LOG_PATH = OUTPUT_ROOT / "run_log.txt"
    MANIFEST_PATH = OUTPUT_ROOT / "species_manifest.csv"


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

    raw_facet_fq = value.get("ala_facet_fq")

    if isinstance(raw_facet_fq, str):
        facet_fq_filters = tuple(
            item.strip() for item in raw_facet_fq.split(" | ") if item.strip()
        )
    elif isinstance(raw_facet_fq, (list, tuple)):
        facet_fq_filters = tuple(
            str(item).strip() for item in raw_facet_fq if str(item).strip()
        )
    else:
        facet_fq_filters = ()

    return SpeciesTarget(
        key=key,
        scientific_name=scientific_name,
        common_name=value.get("common_name"),
        taxon_lsid=value.get("taxon_lsid"),
        source_order=normalise_text_cell(value.get("order")),
        facet_fq_filters=facet_fq_filters,
        target_match_suspect=bool(value.get("target_match_suspect", False)),
    )


def generate_species_targets_file(
    refresh: bool = REFRESH_SPECIES_TARGETS_BEFORE_RUN,
    *,
    order: str | None = None,
) -> None:
    targets_path = generated_species_targets_path(order)

    if targets_path.exists() and not refresh:
        return

    if not SPECIES_TARGETS_GENERATOR_SCRIPT.exists():
        raise FileNotFoundError(
            f"Missing species target generator script: {SPECIES_TARGETS_GENERATOR_SCRIPT}"
        )

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    module = importlib.import_module(SPECIES_TARGETS_GENERATOR_MODULE)

    if not hasattr(module, "generate_species_targets"):
        raise AttributeError(
            "scripts/fetch_by_order.py must define generate_species_targets()."
        )

    if order is None:
        module.generate_species_targets(output_dir=OUTPUT_ROOT)
    else:
        module.generate_species_targets(order=order, output_dir=OUTPUT_ROOT)


def load_generated_species_targets(order: str | None = None) -> list[SpeciesTarget]:
    targets_path = generated_species_targets_path(order)

    if not targets_path.exists():
        raise FileNotFoundError(
            f"Missing generated species targets: {targets_path}. "
            "Run scripts/fetch_by_order.py first."
        )

    generated_targets = json.loads(targets_path.read_text(encoding="utf-8"))

    if not isinstance(generated_targets, list):
        raise ValueError(f"{targets_path} must contain a JSON list of species targets.")

    targets = [coerce_species_target(target) for target in generated_targets]

    if not targets:
        raise ValueError("Generated species target JSON is empty.")

    return targets


def resolve_species_targets(
    species_targets: list[SpeciesTarget | dict[str, Any]] | None = None,
    *,
    refresh_generated_targets: bool = REFRESH_SPECIES_TARGETS_BEFORE_RUN,
    generated_order: str | None = None,
) -> list[SpeciesTarget]:
    if species_targets is not None:
        targets = [coerce_species_target(target) for target in species_targets]
    elif SPECIES_TARGETS:
        targets = [coerce_species_target(target) for target in SPECIES_TARGETS]
    else:
        generate_species_targets_file(
            refresh=refresh_generated_targets,
            order=generated_order,
        )
        targets = load_generated_species_targets(generated_order)

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
        "target_match_suspect": target.target_match_suspect,
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


def warn_if_query_resolves_to_different_taxon(target: SpeciesTarget, data: dict[str, Any]) -> bool:
    if target.taxon_lsid:
        return False

    records = data.get("occurrences", [])

    if not records:
        return False

    first_record = records[0]

    if record_matches_query_name(target, first_record):
        return False

    names = ", ".join(record_taxon_names(first_record)) or "unknown"
    log(
        f"Warning: species={target.key} query_name={target.scientific_name!r} "
        f"resolved first record to different ALA taxon/name(s): {names}. "
        "Prefer a taxon_lsid when strict taxonomy is required."
    )
    return True


# =============================================================================
# QUERY BUILDING
# =============================================================================

def quote_fq(field: str, value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'{field}:"{escaped}"'


def build_query(target: SpeciesTarget) -> str:
    if target.taxon_lsid:
        return f"lsid:{target.taxon_lsid}"

    if target.facet_fq_filters:
        return "*:*"

    return target.scientific_name


def joined_facet_filter(filters: tuple[str, ...]) -> str:
    if len(filters) == 1:
        return filters[0]

    return "(" + " OR ".join(filters) + ")"


def build_fq_filters(target: SpeciesTarget) -> list[str]:
    filters: list[str] = []

    if COUNTRY_FILTER_ENABLED:
        filters.append(COUNTRY_FILTER)

    if target.source_order:
        filters.append(quote_fq("order", target.source_order))

    if target.facet_fq_filters:
        filters.append(joined_facet_filter(target.facet_fq_filters))
        return filters

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
        "resume_cache_version": RESUME_CACHE_VERSION,
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
            "source_order": target.source_order,
            "facet_fq_filters": list(target.facet_fq_filters),
        },
    }


def config_fingerprint(config: dict[str, Any]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resume_config(config: dict[str, Any]) -> dict[str, Any]:
    comparable = dict(config)
    comparable.pop("script_sha256", None)
    return comparable


def configs_equivalent_for_resume(
    existing_config: Any,
    current_config: dict[str, Any],
) -> bool:
    if not isinstance(existing_config, dict):
        return False

    return resume_config(existing_config) == resume_config(current_config)


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
        existing_config = (
            existing_metadata.get("config")
            if isinstance(existing_metadata, dict)
            else None
        )

        if configs_equivalent_for_resume(existing_config, config):
            log(
                f"Species={target.key}: reusing output directory; only script hash changed."
            )
        else:
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


def request_json_with_retries(
    *,
    session: requests.Session,
    params: list[tuple[str, str | int]],
    context: str,
) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = min(120, 10 * attempt)
                log(f"HTTP 429 for {context}. Sleeping {wait}s.")
                response.close()
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()

        except Exception as exc:
            last_error = exc
            wait = min(120, 2**attempt)
            log(
                f"Request failed: {context}, attempt={attempt}/{MAX_RETRIES}, "
                f"error={exc}. Retrying in {wait}s."
            )
            time.sleep(wait)

    raise RuntimeError(f"Failed {context} after {MAX_RETRIES} retries: {last_error}")


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
    return request_json_with_retries(
        session=get_thread_session(),
        params=params,
        context=f"species={target.key}, start={start}",
    )


def fetch_total_records(
    target: SpeciesTarget,
    extra_fq_filters: tuple[str, ...] = (),
    warn_on_taxon_resolution: bool = True,
) -> tuple[int, bool]:
    data = fetch_json(target=target, start=0, page_size=1, extra_fq_filters=extra_fq_filters)
    total = int(data.get("totalRecords", 0))
    target_match_suspect = False

    if warn_on_taxon_resolution:
        target_match_suspect = warn_if_query_resolves_to_different_taxon(target, data)

    if MAX_RECORDS_PER_SPECIES is not None:
        total = min(total, MAX_RECORDS_PER_SPECIES)

    return total, target_match_suspect


def fetch_facet_partitions(
    target: SpeciesTarget,
    facet_field: str,
    *,
    base_label: str | None = None,
    extra_fq_filters: tuple[str, ...] = (),
    facet_limit: int = YEAR_FACET_LIMIT,
) -> list[QueryPartition]:
    params: list[tuple[str, str | int]] = [
        ("q", build_query(target)),
        ("qualityProfile", QUALITY_PROFILE),
        ("qc", QUALITY_CONTROL),
        (START_PARAM_NAME, 0),
        ("pageSize", 0),
        ("sort", ALA_SORT_FIELD),
        ("dir", ALA_SORT_DIRECTION),
        ("facet", "true"),
        ("facets", facet_field),
        ("flimit", facet_limit),
    ]

    for fq in [*build_fq_filters(target), *extra_fq_filters]:
        params.append(("fq", fq))

    data = request_json_with_retries(
        session=get_thread_session(),
        params=params,
        context=f"species={target.key}, facet={facet_field}",
    )
    partitions: list[QueryPartition] = []

    for facet in data.get("facetResults", []):
        if facet.get("fieldName") != facet_field:
            continue

        for result in facet.get("fieldResult", []):
            count = int(result.get("count", 0))
            fq = normalise_text_cell(result.get("fq"))
            label = normalise_text_cell(result.get("label")) or "unknown"

            if count <= 0 or not fq:
                continue

            partitions.append(
                QueryPartition(
                    label=";".join(
                        part for part in (base_label, f"{facet_field}={label}") if part
                    ),
                    extra_fq_filters=(*extra_fq_filters, fq),
                    total_records=count,
                )
            )

    return partitions


def fetch_year_facet_partitions(target: SpeciesTarget) -> list[QueryPartition]:
    return fetch_facet_partitions(target, "year")


def split_oversized_year_partitions(
    target: SpeciesTarget,
    partitions: list[QueryPartition],
) -> list[QueryPartition]:
    out: list[QueryPartition] = []

    for partition in partitions:
        if partition.total_records <= SEARCH_API_MAX_WINDOW:
            out.append(partition)
            continue

        month_partitions = fetch_facet_partitions(
            target,
            "month",
            base_label=partition.label,
            extra_fq_filters=partition.extra_fq_filters,
        )

        if not month_partitions:
            raise RuntimeError(
                f"Species={target.key}: {partition.label} exceeds search API window "
                "and has no month facets."
            )

        split_partitions: list[QueryPartition] = []

        for month_partition in month_partitions:
            if month_partition.total_records <= SEARCH_API_MAX_WINDOW:
                split_partitions.append(month_partition)
                continue

            day_partitions = fetch_facet_partitions(
                target,
                "day",
                base_label=month_partition.label,
                extra_fq_filters=month_partition.extra_fq_filters,
            )

            if not day_partitions:
                raise RuntimeError(
                    f"Species={target.key}: {month_partition.label} exceeds "
                    "search API window and has no day facets."
                )

            oversized_days = [
                day_partition
                for day_partition in day_partitions
                if day_partition.total_records > SEARCH_API_MAX_WINDOW
            ]

            if oversized_days:
                lat_long_partitions = fetch_facet_partitions(
                    target,
                    "lat_long",
                    base_label=month_partition.label,
                    extra_fq_filters=month_partition.extra_fq_filters,
                    facet_limit=max(month_partition.total_records, YEAR_FACET_LIMIT),
                )

                lat_long_total = sum(
                    partition.total_records for partition in lat_long_partitions
                )

                if lat_long_total == month_partition.total_records and all(
                    partition.total_records <= SEARCH_API_MAX_WINDOW
                    for partition in lat_long_partitions
                ):
                    log(
                        f"Species={target.key}: split oversized {month_partition.label} "
                        f"({month_partition.total_records:,}) into "
                        f"{len(lat_long_partitions):,} lat_long partitions."
                    )
                    split_partitions.extend(lat_long_partitions)
                    continue

                labels = ", ".join(
                    f"{p.label} ({p.total_records:,})" for p in oversized_days
                )
                raise RuntimeError(
                    f"Species={target.key}: day partition(s) exceed "
                    f"search API window: {labels}"
                )

            day_total = sum(day_partition.total_records for day_partition in day_partitions)

            if day_total != month_partition.total_records:
                raise RuntimeError(
                    f"Species={target.key}: day partitions for {month_partition.label} "
                    f"sum to {day_total:,}, expected {month_partition.total_records:,}."
                )

            log(
                f"Species={target.key}: split oversized {month_partition.label} "
                f"({month_partition.total_records:,}) into {len(day_partitions):,} "
                "day partitions."
            )
            split_partitions.extend(day_partitions)

        month_total = sum(partition.total_records for partition in split_partitions)

        if month_total != partition.total_records:
            raise RuntimeError(
                f"Species={target.key}: split partitions for {partition.label} "
                f"sum to {month_total:,}, expected {partition.total_records:,}."
            )

        log(
            f"Species={target.key}: split oversized {partition.label} "
            f"({partition.total_records:,}) into {len(month_partitions):,} month partitions."
        )
        out.extend(split_partitions)

    return out


def first_window_partition(total_records: int) -> QueryPartition:
    return QueryPartition(
        label="all",
        extra_fq_filters=(),
        total_records=min(total_records, SEARCH_API_MAX_WINDOW),
    )


def make_query_partition_plan(target: SpeciesTarget, total_records: int) -> QueryPartitionPlan:
    if total_records <= SEARCH_API_MAX_WINDOW:
        partitions = [first_window_partition(total_records)]
        return QueryPartitionPlan(
            partitions=partitions,
            coverage_status="complete",
            coverage_detail=None,
            expected_total_records=total_records,
            planned_partition_records=total_records,
        )

    partitions = fetch_year_facet_partitions(target)

    if not partitions:
        detail = (
            "no year facets available; only the first "
            f"{SEARCH_API_MAX_WINDOW:,} unpartitioned rows can be fetched"
        )
        log(f"Species={target.key}: warning: {detail}.")
        fallback = first_window_partition(total_records)
        return QueryPartitionPlan(
            partitions=[fallback],
            coverage_status="truncated_no_year_facets",
            coverage_detail=detail,
            expected_total_records=total_records,
            planned_partition_records=fallback.total_records,
        )

    try:
        partitions = split_oversized_year_partitions(target, partitions)
    except RuntimeError as exc:
        detail = f"partition split failed: {exc}"
        log(f"Species={target.key}: warning: {detail}")
        fallback = first_window_partition(total_records)
        return QueryPartitionPlan(
            partitions=[fallback],
            coverage_status="truncated_partition_split_failed",
            coverage_detail=detail,
            expected_total_records=total_records,
            planned_partition_records=fallback.total_records,
        )

    partition_total = sum(partition.total_records for partition in partitions)
    log(
        f"Species={target.key}: split {total_records:,} records into "
        f"{len(partitions):,} year partitions; partition_total={partition_total:,}"
    )

    if partition_total != total_records:
        detail = (
            f"year partition coverage total={partition_total:,}, "
            f"expected={total_records:,}"
        )
        log(f"Species={target.key}: warning: {detail}.")

        if partition_total < total_records:
            partitions = [*partitions, first_window_partition(total_records)]

        return QueryPartitionPlan(
            partitions=partitions,
            coverage_status="partition_total_mismatch",
            coverage_detail=detail,
            expected_total_records=total_records,
            planned_partition_records=sum(partition.total_records for partition in partitions),
        )

    return QueryPartitionPlan(
        partitions=partitions,
        coverage_status="complete",
        coverage_detail=None,
        expected_total_records=total_records,
        planned_partition_records=partition_total,
    )


def make_query_partitions(target: SpeciesTarget, total_records: int) -> list[QueryPartition]:
    return make_query_partition_plan(target, total_records).partitions


# =============================================================================
# SHARD WRITING
# =============================================================================

def write_page_shard(task: PageTask) -> PageResult:
    target = task.target
    path = shard_path(target, task.page_index)

    if path.exists() and path.stat().st_size > 0:
        try:
            existing_rows = pl.scan_parquet(path).select(pl.len()).collect().item()

            if int(existing_rows) == task.page_size:
                return PageResult(
                    species_key=target.key,
                    page_index=task.page_index,
                    start=task.start,
                    count=int(existing_rows),
                    shard_path=path,
                )

            log(
                f"Species={target.key}: cached shard page={task.page_index} has "
                f"rows={int(existing_rows):,}, expected={task.page_size:,}; refetching."
            )
            path.unlink(missing_ok=True)
        except Exception:
            path.unlink(missing_ok=True)

    data: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    validation_status = "complete"
    validation_detail: str | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        data = fetch_json(
            target=target,
            start=task.start,
            page_size=task.page_size,
            extra_fq_filters=task.extra_fq_filters,
        )
        records = data.get("occurrences", [])

        if len(records) == task.page_size:
            validation_detail = None
            break

        validation_detail = (
            f"page={task.page_index} start={task.start} rows={len(records):,}, "
            f"expected={task.page_size:,}"
        )

        if attempt < MAX_RETRIES:
            wait = min(120, 2**attempt)
            log(
                f"Species={target.key}: partial page response ({validation_detail}); "
                f"retrying page validation attempt={attempt}/{MAX_RETRIES} in {wait}s."
            )
            time.sleep(wait)
            continue

        validation_status = "partial_row_count"
        log(
            f"Species={target.key}: warning: keeping partial page after retries "
            f"({validation_detail})."
        )

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
        validation_status=validation_status,
        validation_detail=validation_detail,
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
                        f"Species={target.key}: warning: page failed after retries; "
                        f"keeping run alive with empty shard. page={task.page_index}, "
                        f"start={task.start}, error={exc}"
                    )
                    path = shard_path(target, task.page_index)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    pl.DataFrame([], schema=SCHEMA).write_parquet(path)
                    result = PageResult(
                        species_key=target.key,
                        page_index=task.page_index,
                        start=task.start,
                        count=0,
                        shard_path=path,
                        validation_status="fetch_failed",
                        validation_detail=str(exc),
                    )

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
                "target_match_suspect",
                "config_fingerprint",
                "query",
                "fq_filters",
                "reported_total_records",
                "planned_partition_records",
                "partition_coverage_status",
                "partition_coverage_detail",
                "page_validation_issue_count",
                "page_validation_detail",
                "fetch_status",
                "fetch_error",
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
                    "target_match_suspect": result.target_match_suspect,
                    "config_fingerprint": result.config_fingerprint,
                    "query": build_query(target),
                    "fq_filters": " | ".join(build_fq_filters(target)),
                    "reported_total_records": result.reported_total_records,
                    "planned_partition_records": result.planned_partition_records,
                    "partition_coverage_status": result.partition_coverage_status,
                    "partition_coverage_detail": result.partition_coverage_detail or "",
                    "page_validation_issue_count": result.page_validation_issue_count,
                    "page_validation_detail": result.page_validation_detail or "",
                    "fetch_status": result.fetch_status,
                    "fetch_error": result.fetch_error or "",
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


def validate_run_settings() -> None:
    if MAX_RECORDS_PER_SPECIES is None:
        return

    if MAX_RECORDS_PER_SPECIES <= 0:
        raise ValueError("MAX_RECORDS_PER_SPECIES must be positive or None.")

    if MAX_RECORDS_PER_SPECIES > SEARCH_API_MAX_WINDOW:
        raise ValueError(
            "MAX_RECORDS_PER_SPECIES must not exceed SEARCH_API_MAX_WINDOW; "
            "larger test caps can hide partitioning mistakes."
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

    total_records, target_match_suspect = fetch_total_records(target)

    if target_match_suspect:
        target = replace(target, target_match_suspect=True)

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
            target_match_suspect=target.target_match_suspect,
            planned_partition_records=0,
        )

    partition_plan = make_query_partition_plan(target, total_records)
    tasks = make_tasks(target, partition_plan.partitions)
    log(
        f"Species={target.key}: pages to fetch={len(tasks):,}; "
        f"partitions={len(partition_plan.partitions):,}"
    )

    page_results = run_parallel_fetch_for_species(target, tasks)
    rows_written = sum(result.count for result in page_results)
    page_validation_issues = [
        result for result in page_results if result.validation_status != "complete"
    ]
    page_validation_detail = " | ".join(
        result.validation_detail or result.validation_status
        for result in page_validation_issues
    ) or None

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
        target_match_suspect=target.target_match_suspect,
        partition_coverage_status=partition_plan.coverage_status,
        partition_coverage_detail=partition_plan.coverage_detail,
        planned_partition_records=partition_plan.planned_partition_records,
        page_validation_issue_count=len(page_validation_issues),
        page_validation_detail=page_validation_detail,
    )


def failed_species_result(target: SpeciesTarget, error: Exception) -> SpeciesResult:
    species_output = species_parquet_path(target)
    species_output.parent.mkdir(parents=True, exist_ok=True)

    if not species_output.exists():
        pl.DataFrame([], schema=SCHEMA).write_parquet(species_output)

    return SpeciesResult(
        species_key=target.key,
        scientific_name=target.scientific_name,
        common_name=target.common_name,
        taxon_lsid=target.taxon_lsid,
        config_fingerprint=config_fingerprint(species_run_config(target)),
        reported_total_records=0,
        pages_written=0,
        rows_written=0,
        elapsed_seconds=0.0,
        species_parquet_path=species_output,
        fetch_status="failed",
        fetch_error=str(error),
    )


def run_alascraper(
    *,
    species_targets: list[SpeciesTarget | dict[str, Any]] | None = None,
    write_csv: bool | None = None,
    refresh_generated_targets: bool = REFRESH_SPECIES_TARGETS_BEFORE_RUN,
    order: str | None = None,
    dataset_class: str | None = None,
) -> int:
    global WRITE_CSV

    if write_csv is not None:
        WRITE_CSV = write_csv

    configure_output_root(dataset_output_root(order, dataset_class))

    run_started = time.perf_counter()
    validate_privacy_settings()
    validate_run_settings()
    prepare_output_dirs()

    if species_targets is not None:
        active_targets = resolve_species_targets(
            species_targets,
            refresh_generated_targets=False,
        )
    else:
        active_targets = resolve_species_targets(
            refresh_generated_targets=refresh_generated_targets,
            generated_order=order,
        )

    log("Starting ALA species-by-species occurrence fetch.")
    log(f"Python version: {sys.version.split()[0]}")
    log(f"Dataset output root: {OUTPUT_ROOT}")
    log(f"Dataset class: {dataset_class or 'misc'}")
    if order is not None:
        log(f"Generated target order: {order}")
    log(f"Species targets: {len(active_targets):,}")
    log(f"Workers: {WORKERS}")
    log(f"Page size: {PAGE_SIZE}")
    log(f"CSV output enabled: {WRITE_CSV}")
    log(f"User-data fields enabled: {INCLUDE_USER_DATA_FIELDS}")

    species_results: list[SpeciesResult] = []

    for target in active_targets:
        try:
            result = fetch_one_species(target)
        except Exception as exc:
            log(
                f"Species={target.key}: failed after retries; continuing with next "
                f"target. error={exc}"
            )
            result = failed_species_result(target, exc)

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


def parse_bool(value: str) -> bool:
    normalised = value.strip().lower()

    if normalised in {"1", "true", "t", "yes", "y"}:
        return True

    if normalised in {"0", "false", "f", "no", "n"}:
        return False

    raise argparse.ArgumentTypeError(
        "Expected TRUE or FALSE for optional CSV output flag."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Atlas of Living Australia occurrence records species by species."
    )
    parser.add_argument(
        "--order",
        default=None,
        help=(
            "ALA order used to refresh generated species targets before the run, "
            "for example Lepidoptera, Neuroptera, Diptera, or Mantodea."
        ),
    )
    parser.add_argument(
        "--class",
        dest="dataset_class",
        default=None,
        help=(
            "Dataset class/group folder name under datasets/. "
            "If omitted, results go under datasets/misc/."
        ),
    )
    parser.add_argument(
        "write_csv",
        nargs="?",
        type=parse_bool,
        default=None,
        metavar="WRITE_CSV",
        help=(
            "Optional TRUE/FALSE toggle for writing ala_species_records.csv "
            "alongside Parquet in the dataset folder."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_alascraper(
        order=args.order,
        dataset_class=args.dataset_class,
        write_csv=args.write_csv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
