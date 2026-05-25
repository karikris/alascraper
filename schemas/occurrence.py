from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl


CANONICAL_OCCURRENCE_SCHEMA: dict[str, pl.DataType] = {
    "source": pl.String,
    "source_jurisdiction": pl.String,
    "source_dataset": pl.String,
    "source_record_id": pl.String,
    "source_record_url": pl.String,
    "source_updated_at": pl.String,
    "ingested_at_utc": pl.String,
    "occurrence_id": pl.String,
    "catalog_number": pl.String,
    "scientific_name": pl.String,
    "vernacular_name": pl.String,
    "taxon_rank": pl.String,
    "kingdom": pl.String,
    "class_name": pl.String,
    "order": pl.String,
    "family": pl.String,
    "genus": pl.String,
    "specific_epithet": pl.String,
    "infraspecific_epithet": pl.String,
    "country": pl.String,
    "country_code": pl.String,
    "state_province": pl.String,
    "decimal_latitude": pl.Float64,
    "decimal_longitude": pl.Float64,
    "coordinate_uncertainty_m": pl.Float64,
    "coordinate_precision": pl.String,
    "geodetic_datum": pl.String,
    "event_date": pl.String,
    "event_date_start": pl.String,
    "event_date_end": pl.String,
    "year": pl.Int64,
    "basis_of_record": pl.String,
    "occurrence_status": pl.String,
    "record_status": pl.String,
    "individual_count": pl.Float64,
    "observation_type": pl.String,
    "establishment_means": pl.String,
    "state_conservation": pl.String,
    "country_conservation": pl.String,
    "sensitive_record": pl.Boolean,
    "spatially_generalised": pl.Boolean,
    "data_generalizations": pl.String,
    "information_withheld": pl.String,
    "license": pl.String,
    "rights_holder": pl.String,
    "raw_source_payload": pl.String,
    "raw_record_hash": pl.String,
    "canonical_record_hash": pl.String,
}

CANONICAL_OCCURRENCE_FIELDS = tuple(CANONICAL_OCCURRENCE_SCHEMA)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def parse_event_date(value: Any) -> tuple[str | None, str | None, int | None]:
    text = clean_text(value)
    if not text:
        return None, None, None

    start, end = (text.split("/", 1) + [None])[:2] if "/" in text else (text, None)
    start = clean_text(start)
    end = clean_text(end)
    year = to_int(start[:4]) if start and len(start) >= 4 else None
    return start, end, year


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_payload(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def canonical_hash(row: dict[str, Any]) -> str:
    payload = {
        "source": row.get("source"),
        "source_jurisdiction": row.get("source_jurisdiction"),
        "source_record_id": row.get("source_record_id"),
        "scientific_name": row.get("scientific_name"),
        "event_date_start": row.get("event_date_start"),
        "decimal_latitude": row.get("decimal_latitude"),
        "decimal_longitude": row.get("decimal_longitude"),
    }
    return hash_payload(payload)


def canonical_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    normalised_rows = []
    for row in rows:
        complete_row = {field: row.get(field) for field in CANONICAL_OCCURRENCE_FIELDS}
        normalised_rows.append(complete_row)
    return pl.DataFrame(
        normalised_rows,
        schema=CANONICAL_OCCURRENCE_SCHEMA,
        orient="row",
    )


def write_occurrence_parquet(rows: list[dict[str, Any]], path: Path) -> pl.DataFrame:
    frame = canonical_frame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
    return frame
