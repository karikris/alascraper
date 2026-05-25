#!/usr/bin/env python3.14
"""Enrich butterfly occurrence records with EPBC and state conservation status."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl


DEFAULT_SOURCE_PATH = Path("datasets/insecta/lepidoptera/butterflies_cleaned.parquet")
DEFAULT_REFERENCE_PATH = Path("data/reference/butterfly_conservation_status.csv")
DEFAULT_OUTPUT_PATH = Path("datasets/insecta/lepidoptera/butterflies_conservation.parquet")
DEFAULT_REPORT_PATH = Path(
    "datasets/insecta/lepidoptera/quality_reports/butterflies_conservation_status_report.json"
)

REFERENCE_COLUMNS = [
    "accepted_taxon",
    "match_names",
    "rank",
    "common_name",
    "epbc_status",
    "epbc_listed_id",
    "epbc_sprat_url",
    "epbc_conservation_advice_url",
    "epbc_recovery_plan_url",
    "epbc_protected_matters_url",
    "state_status",
    "state_status_jurisdiction",
    "state_source_url",
    "source_dataset",
    "source_date",
    "notes",
]

CONSERVATION_OUTPUT_COLUMNS = [
    "Status",
    "epbc_status",
    "epbc_listed_taxon",
    "epbc_common_name",
    "epbc_listed_id",
    "epbc_sprat_url",
    "epbc_conservation_advice_url",
    "epbc_recovery_plan_url",
    "epbc_protected_matters_url",
    "epbc_source_dataset",
    "epbc_source_date",
    "epbc_match_type",
    "epbc_match_name",
    "epbc_match_confidence",
    "epbc_notes",
    "state_status",
    "state_status_jurisdiction",
    "state_status_level",
    "state_status_for_occurrence",
    "state_status_jurisdiction_matched",
    "state_status_qualifier",
    "state_listed_taxon",
    "state_common_name",
    "state_source_url",
    "state_source_date",
    "state_match_type",
    "state_match_name",
    "state_match_confidence",
    "state_notes",
]

FLOAT_OUTPUT_COLUMNS = {"epbc_match_confidence", "state_match_confidence"}
OUTPUT_SCHEMA = {
    column: pl.Float64 if column in FLOAT_OUTPUT_COLUMNS else pl.String
    for column in CONSERVATION_OUTPUT_COLUMNS
}
STATE_PROVINCE_CODES = {
    "Australian Capital Territory": "ACT",
    "New South Wales": "NSW",
    "Northern Territory": "NT",
    "Queensland": "QLD",
    "South Australia": "SA",
    "Tasmania": "TAS",
    "Victoria": "VIC",
    "Western Australia": "WA",
    "ACT": "ACT",
    "NSW": "NSW",
    "NT": "NT",
    "QLD": "QLD",
    "SA": "SA",
    "TAS": "TAS",
    "VIC": "VIC",
    "WA": "WA",
}
STATE_STATUS_COLUMNS = [
    "state_status_level",
    "state_status_for_occurrence",
    "state_status_jurisdiction_matched",
    "state_status_qualifier",
]
STATE_STATUS_LEVELS = (
    "Critically Endangered",
    "Endangered",
    "Vulnerable",
    "Rare",
)


@dataclass(frozen=True)
class EnrichmentOutputs:
    output_parquet: Path
    report_json: Path | None
    row_count: int
    epbc_matched_rows: int
    state_matched_rows: int
    reference_rows: int
    match_name_count: int


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_reference(path: Path) -> pl.DataFrame:
    reference = pl.read_csv(path, infer_schema_length=0)
    for column in REFERENCE_COLUMNS:
        if column not in reference.columns:
            reference = reference.with_columns(pl.lit(None, dtype=pl.String).alias(column))
    return reference.select(REFERENCE_COLUMNS).with_columns(
        [
            pl.when(pl.col(column).str.strip_chars() == "")
            .then(None)
            .otherwise(pl.col(column).str.strip_chars())
            .alias(column)
            for column in REFERENCE_COLUMNS
        ]
    )


def split_match_names(reference_row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    accepted_taxon = clean_text(reference_row.get("accepted_taxon"))
    if accepted_taxon:
        values.append(accepted_taxon)
    for value in (clean_text(reference_row.get("match_names")) or "").split("|"):
        cleaned = clean_text(value)
        if cleaned:
            values.append(cleaned)
    return list(dict.fromkeys(values))


def status_level_and_qualifier(status_text: str | None) -> tuple[str | None, str | None]:
    status = clean_text(status_text)
    if not status:
        return None, None
    for level in STATE_STATUS_LEVELS:
        if status == level:
            return level, None
        if status.startswith(level):
            qualifier = clean_text(status[len(level) :].strip(" -;"))
            return level, qualifier
    return status, None


def parse_state_status_entries(state_status: Any) -> list[dict[str, str | None]]:
    text = clean_text(state_status)
    if not text:
        return []
    entries = []
    for part in text.split(";"):
        entry = clean_text(part)
        if not entry:
            continue
        jurisdiction = None
        status_text = entry
        if ":" in entry:
            jurisdiction_text, status_text = entry.split(":", 1)
            jurisdiction = clean_text(jurisdiction_text)
            status_text = clean_text(status_text) or ""
        level, qualifier = status_level_and_qualifier(status_text)
        entries.append(
            {
                "jurisdiction": jurisdiction,
                "status_text": status_text,
                "status_level": level,
                "qualifier": qualifier,
            }
        )
    return entries


def state_status_info(state_province: Any, state_status: Any) -> dict[str, str | None]:
    state_name = clean_text(state_province)
    state_code = STATE_PROVINCE_CODES.get(state_name or "")
    entries = parse_state_status_entries(state_status)
    matching_entries = [
        entry
        for entry in entries
        if entry["jurisdiction"] is None or entry["jurisdiction"] == state_code
    ]
    if not matching_entries:
        return {column: None for column in STATE_STATUS_COLUMNS}

    entry = matching_entries[0]
    jurisdiction = entry["jurisdiction"] or state_code
    status_text = entry["status_text"]
    status_for_occurrence = (
        f"{jurisdiction}: {status_text}" if jurisdiction and status_text else status_text
    )
    return {
        "state_status_level": entry["status_level"],
        "state_status_for_occurrence": status_for_occurrence,
        "state_status_jurisdiction_matched": jurisdiction,
        "state_status_qualifier": entry["qualifier"],
    }


def match_confidence(match_field: str, *, synonym: bool) -> float:
    if match_field == "scientificName" and not synonym:
        return 1.0
    if match_field == "scientificName" and synonym:
        return 0.95
    if match_field == "species" and not synonym:
        return 0.9
    return 0.85


def build_match_index(reference: pl.DataFrame) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for reference_row in reference.iter_rows(named=True):
        accepted_taxon = clean_text(reference_row.get("accepted_taxon"))
        if not accepted_taxon:
            continue
        for match_name in split_match_names(reference_row):
            index.setdefault(match_name, reference_row)
    return index


def build_match_frame(reference: pl.DataFrame) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for reference_row in reference.iter_rows(named=True):
        for match_name in split_match_names(reference_row):
            rows.append({"match_name": match_name, **reference_row})
    if not rows:
        return pl.DataFrame(
            schema={"match_name": pl.String, **{column: pl.String for column in REFERENCE_COLUMNS}}
        )
    return pl.DataFrame(rows).unique(
        subset=["match_name"],
        keep="first",
        maintain_order=True,
    )


def match_type_expr() -> pl.Expr:
    is_synonym = pl.col("match_name") != pl.col("accepted_taxon")
    return (
        pl.when(is_synonym)
        .then(pl.concat_str([pl.col("_match_field"), pl.lit("_synonym")]))
        .otherwise(pl.col("_match_field"))
    )


def match_confidence_expr() -> pl.Expr:
    is_scientific_name = pl.col("_match_field") == "scientificName"
    is_synonym = pl.col("match_name") != pl.col("accepted_taxon")
    return (
        pl.when(is_scientific_name & ~is_synonym)
        .then(1.0)
        .when(is_scientific_name & is_synonym)
        .then(0.95)
        .when(~is_scientific_name & ~is_synonym)
        .then(0.9)
        .otherwise(0.85)
    )


def field_match_frame(
    source_frame: pl.DataFrame,
    match_frame: pl.DataFrame,
    *,
    field: str,
    priority: int,
) -> pl.DataFrame:
    return (
        source_frame.select(
            [
                "_occurrence_row",
                "stateProvince",
                pl.col(field).cast(pl.String).str.strip_chars().alias("match_name"),
            ]
        )
        .filter(pl.col("match_name").is_not_null() & (pl.col("match_name") != ""))
        .join(match_frame, on="match_name", how="inner")
        .with_columns(
            [
                pl.lit(field).alias("_match_field"),
                pl.lit(priority).alias("_match_priority"),
            ]
        )
    )


def build_annotation_frame(
    source_frame: pl.DataFrame,
    match_frame: pl.DataFrame,
) -> pl.DataFrame:
    indexed_source = source_frame.with_row_index("_occurrence_row")
    matches = pl.concat(
        [
            field_match_frame(
                indexed_source,
                match_frame,
                field="scientificName",
                priority=0,
            ),
            field_match_frame(indexed_source, match_frame, field="species", priority=1),
        ],
        how="vertical_relaxed",
    )
    best_matches = matches.sort(["_occurrence_row", "_match_priority"]).unique(
        subset=["_occurrence_row"],
        keep="first",
        maintain_order=True,
    )
    state_status_frame = pl.DataFrame(
        [
            state_status_info(row["stateProvince"], row["state_status"])
            for row in best_matches.select(["stateProvince", "state_status"]).iter_rows(
                named=True
            )
        ],
        schema={column: pl.String for column in STATE_STATUS_COLUMNS},
        orient="row",
    )
    best_matches = pl.concat([best_matches, state_status_frame], how="horizontal")
    annotated_matches = best_matches.with_columns(
        [
            match_type_expr().alias("_match_type"),
            match_confidence_expr().alias("_match_confidence"),
        ]
    ).select(
        [
            "_occurrence_row",
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("epbc_status"))
            .otherwise(None)
            .alias("Status"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("epbc_status"))
            .otherwise(None)
            .alias("epbc_status"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("accepted_taxon"))
            .otherwise(None)
            .alias("epbc_listed_taxon"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("common_name"))
            .otherwise(None)
            .alias("epbc_common_name"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("epbc_listed_id"))
            .otherwise(None)
            .alias("epbc_listed_id"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("epbc_sprat_url"))
            .otherwise(None)
            .alias("epbc_sprat_url"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("epbc_conservation_advice_url"))
            .otherwise(None)
            .alias("epbc_conservation_advice_url"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("epbc_recovery_plan_url"))
            .otherwise(None)
            .alias("epbc_recovery_plan_url"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("epbc_protected_matters_url"))
            .otherwise(None)
            .alias("epbc_protected_matters_url"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("source_dataset"))
            .otherwise(None)
            .alias("epbc_source_dataset"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("source_date"))
            .otherwise(None)
            .alias("epbc_source_date"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("_match_type"))
            .otherwise(None)
            .alias("epbc_match_type"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("match_name"))
            .otherwise(None)
            .alias("epbc_match_name"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("_match_confidence"))
            .otherwise(None)
            .alias("epbc_match_confidence"),
            pl.when(pl.col("epbc_status").is_not_null())
            .then(pl.col("notes"))
            .otherwise(None)
            .alias("epbc_notes"),
            pl.when(pl.col("state_status").is_not_null())
            .then(pl.col("state_status"))
            .otherwise(None)
            .alias("state_status"),
            pl.when(pl.col("state_status").is_not_null())
            .then(pl.col("state_status_jurisdiction"))
            .otherwise(None)
            .alias("state_status_jurisdiction"),
            pl.col("state_status_level"),
            pl.col("state_status_for_occurrence"),
            pl.col("state_status_jurisdiction_matched"),
            pl.col("state_status_qualifier"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("accepted_taxon"))
            .otherwise(None)
            .alias("state_listed_taxon"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("common_name"))
            .otherwise(None)
            .alias("state_common_name"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("state_source_url"))
            .otherwise(None)
            .alias("state_source_url"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("source_date"))
            .otherwise(None)
            .alias("state_source_date"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("_match_type"))
            .otherwise(None)
            .alias("state_match_type"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("match_name"))
            .otherwise(None)
            .alias("state_match_name"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("_match_confidence"))
            .otherwise(None)
            .alias("state_match_confidence"),
            pl.when(pl.col("state_status_level").is_not_null())
            .then(pl.col("notes"))
            .otherwise(None)
            .alias("state_notes"),
        ]
    )
    return (
        indexed_source.select("_occurrence_row")
        .join(annotated_matches, on="_occurrence_row", how="left")
        .select(CONSERVATION_OUTPUT_COLUMNS)
        .with_columns(
            [
                pl.col(column).cast(dtype).alias(column)
                for column, dtype in OUTPUT_SCHEMA.items()
            ]
        )
    )


def find_reference_match(
    row: dict[str, Any],
    match_index: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None, str | None, bool]:
    for field in ["scientificName", "species"]:
        candidate = clean_text(row.get(field))
        if not candidate or candidate not in match_index:
            continue
        reference_row = match_index[candidate]
        accepted_taxon = clean_text(reference_row.get("accepted_taxon"))
        return reference_row, field, candidate, candidate != accepted_taxon
    return None, None, None, False


def annotate_row(
    row: dict[str, Any],
    match_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reference_row, match_field, match_name, synonym = find_reference_match(row, match_index)
    annotation = {column: None for column in CONSERVATION_OUTPUT_COLUMNS}
    if reference_row is None or match_field is None or match_name is None:
        return annotation

    accepted_taxon = clean_text(reference_row.get("accepted_taxon"))
    common_name = clean_text(reference_row.get("common_name"))
    source_dataset = clean_text(reference_row.get("source_dataset"))
    source_date = clean_text(reference_row.get("source_date"))
    notes = clean_text(reference_row.get("notes"))
    confidence = match_confidence(match_field, synonym=synonym)
    match_type = f"{match_field}_synonym" if synonym else match_field

    epbc_status = clean_text(reference_row.get("epbc_status"))
    if epbc_status:
        annotation.update(
            {
                "Status": epbc_status,
                "epbc_status": epbc_status,
                "epbc_listed_taxon": accepted_taxon,
                "epbc_common_name": common_name,
                "epbc_listed_id": clean_text(reference_row.get("epbc_listed_id")),
                "epbc_sprat_url": clean_text(reference_row.get("epbc_sprat_url")),
                "epbc_conservation_advice_url": clean_text(
                    reference_row.get("epbc_conservation_advice_url")
                ),
                "epbc_recovery_plan_url": clean_text(
                    reference_row.get("epbc_recovery_plan_url")
                ),
                "epbc_protected_matters_url": clean_text(
                    reference_row.get("epbc_protected_matters_url")
                ),
                "epbc_source_dataset": source_dataset,
                "epbc_source_date": source_date,
                "epbc_match_type": match_type,
                "epbc_match_name": match_name,
                "epbc_match_confidence": confidence,
                "epbc_notes": notes,
            }
        )

    state_status = clean_text(reference_row.get("state_status"))
    if state_status:
        annotation.update(
            {
                "state_status": state_status,
                "state_status_jurisdiction": clean_text(
                    reference_row.get("state_status_jurisdiction")
                ),
                "state_listed_taxon": accepted_taxon,
                "state_common_name": common_name,
                "state_source_url": clean_text(reference_row.get("state_source_url")),
                "state_source_date": source_date,
                "state_match_type": match_type,
                "state_match_name": match_name,
                "state_match_confidence": confidence,
                "state_notes": notes,
            }
        )

    return annotation


def write_report(
    *,
    path: Path,
    source_path: Path,
    reference_path: Path,
    output_path: Path,
    row_count: int,
    epbc_matched_rows: int,
    state_matched_rows: int,
    reference_rows: int,
    match_name_count: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "built_at_utc": utc_timestamp(),
        "source_path": str(source_path),
        "reference_path": str(reference_path),
        "output_path": str(output_path),
        "row_count": row_count,
        "epbc_matched_rows": epbc_matched_rows,
        "state_matched_rows": state_matched_rows,
        "reference_rows": reference_rows,
        "match_name_count": match_name_count,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def enrich_conservation_status(
    *,
    source_path: Path = DEFAULT_SOURCE_PATH,
    reference_path: Path = DEFAULT_REFERENCE_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    report_path: Path | None = DEFAULT_REPORT_PATH,
) -> EnrichmentOutputs:
    source = Path(source_path)
    reference_file = Path(reference_path)
    output = Path(output_path)
    reference = load_reference(reference_file)
    match_frame = build_match_frame(reference)
    source_frame = pl.read_parquet(source)
    annotation_frame = build_annotation_frame(source_frame, match_frame)
    enriched = pl.concat([source_frame, annotation_frame], how="horizontal")
    output.parent.mkdir(parents=True, exist_ok=True)
    enriched.write_parquet(output)

    epbc_matched_rows = int(annotation_frame["Status"].is_not_null().sum())
    state_matched_rows = int(annotation_frame["state_status_level"].is_not_null().sum())
    report = Path(report_path) if report_path else None
    if report is not None:
        write_report(
            path=report,
            source_path=source,
            reference_path=reference_file,
            output_path=output,
            row_count=source_frame.height,
            epbc_matched_rows=epbc_matched_rows,
            state_matched_rows=state_matched_rows,
            reference_rows=reference.height,
            match_name_count=match_frame.height,
        )

    return EnrichmentOutputs(
        output_parquet=output,
        report_json=report,
        row_count=source_frame.height,
        epbc_matched_rows=epbc_matched_rows,
        state_matched_rows=state_matched_rows,
        reference_rows=reference.height,
        match_name_count=match_frame.height,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich butterfly occurrences with EPBC and state conservation status."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = enrich_conservation_status(
        source_path=args.source,
        reference_path=args.reference,
        output_path=args.output,
        report_path=args.report,
    )
    print(f"Wrote enriched parquet: {outputs.output_parquet}")
    if outputs.report_json:
        print(f"Wrote enrichment report: {outputs.report_json}")
    print(
        f"Rows: {outputs.row_count:,}; "
        f"EPBC matched: {outputs.epbc_matched_rows:,}; "
        f"state matched: {outputs.state_matched_rows:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
