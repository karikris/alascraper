#!/usr/bin/env python3.14
"""
Profile family-level ALA occurrence Parquet outputs.

The script discovers compact family outputs written as:

    datasets/<class>/<order>/<family>/<family>.parquet

It then writes quality reports beside each processed family parquet and, by
default, prompts for reviewer notes before moving to the next family.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl


DEFAULT_DATASET_ROOT = Path("datasets")
DEFAULT_OUTPUT_DIR_NAME = "quality_reports"
DEFAULT_NOTES_FILENAME = "family_notes.md"
TOP_VALUE_LIMIT = 10
HIGH_NULL_PERCENT = 90.0


@dataclass(frozen=True)
class FamilyParquet:
    class_key: str
    order_key: str
    family_key: str
    path: Path


@dataclass(frozen=True)
class QualityReport:
    summary: dict[str, Any]
    column_profile: list[dict[str, Any]]
    numeric_stats: list[dict[str, Any]]
    categorical_top_values: list[dict[str, Any]]
    species_counts: dict[str, int]
    quality_flags: list[str]


@dataclass(frozen=True)
class ReportOutputs:
    summary_json: Path
    column_profile_csv: Path
    numeric_stats_csv: Path
    categorical_top_values_csv: Path
    notes_path: Path


def safe_key(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def sql_string(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def is_family_level_parquet(path: Path, dataset_root: Path) -> bool:
    if path.suffix != ".parquet":
        return False

    relative_parts = path.relative_to(dataset_root).parts
    if len(relative_parts) != 4:
        return False

    if any(part in {".scratch", "species", "quality_reports"} for part in relative_parts):
        return False

    family_key = relative_parts[2]
    return path.stem == family_key


def discover_family_parquets(
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    *,
    taxon_class: str | None = None,
    order: str | None = None,
    family: str | None = None,
) -> list[FamilyParquet]:
    root = Path(dataset_root)
    if not root.exists():
        return []

    class_filter = safe_key(taxon_class)
    order_filter = safe_key(order)
    family_filter = safe_key(family)
    matches: list[FamilyParquet] = []

    for path in sorted(root.glob("*/*/*/*.parquet")):
        if not is_family_level_parquet(path, root):
            continue

        class_key, order_key, family_key, _filename = path.relative_to(root).parts
        if class_filter and class_key != class_filter:
            continue
        if order_filter and order_key != order_filter:
            continue
        if family_filter and family_key != family_filter and path.stem != family_filter:
            continue

        matches.append(
            FamilyParquet(
                class_key=class_key,
                order_key=order_key,
                family_key=family_key,
                path=path,
            )
        )

    return matches


def duckdb_row_count(path: Path) -> int:
    con = duckdb.connect(":memory:")
    try:
        return int(
            con.execute(
                f"SELECT count(*) FROM read_parquet({sql_string(path)})"
            ).fetchone()[0]
        )
    finally:
        con.close()


def collect_schema(lazy_frame: pl.LazyFrame) -> dict[str, pl.DataType]:
    return dict(lazy_frame.collect_schema().items())


def collect_column_basics(
    lazy_frame: pl.LazyFrame,
    columns: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    if not columns:
        return {}, {}

    null_counts = lazy_frame.select(
        [pl.col(column).null_count().alias(column) for column in columns]
    ).collect()
    distinct_counts = lazy_frame.select(
        [pl.col(column).n_unique().alias(column) for column in columns]
    ).collect()

    return (
        {column: int(null_counts[column][0]) for column in columns},
        {column: int(distinct_counts[column][0]) for column in columns},
    )


def build_column_profile(
    *,
    schema: dict[str, pl.DataType],
    row_count: int,
    null_counts: dict[str, int],
    distinct_counts: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for column, dtype in schema.items():
        null_count = null_counts.get(column, 0)
        non_null_count = row_count - null_count
        null_percent = (null_count / row_count * 100.0) if row_count else 0.0
        distinct_count = distinct_counts.get(column, 0)
        flags: list[str] = []

        if row_count > 0 and non_null_count == 0:
            flags.append("empty_column")
        if row_count > 0 and null_percent >= HIGH_NULL_PERCENT:
            flags.append("high_null")
        if row_count > 0 and distinct_count <= 1:
            flags.append("single_value")

        rows.append(
            {
                "column": column,
                "dtype": str(dtype),
                "null_count": null_count,
                "null_percent": round(null_percent, 4),
                "non_null_count": non_null_count,
                "distinct_count": distinct_count,
                "flags": "|".join(flags),
            }
        )

    return rows


def collect_numeric_stats(
    lazy_frame: pl.LazyFrame,
    schema: dict[str, pl.DataType],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for column, dtype in schema.items():
        if not dtype.is_numeric():
            continue

        stats = lazy_frame.select(
            [
                pl.col(column).count().alias("non_null_count"),
                pl.col(column).min().alias("min"),
                pl.col(column).max().alias("max"),
                pl.col(column).mean().alias("mean"),
                pl.col(column).median().alias("median"),
                pl.col(column).std().alias("std"),
            ]
        ).collect()
        row = stats.row(0, named=True)
        rows.append(
            {
                "column": column,
                "dtype": str(dtype),
                "non_null_count": int(row["non_null_count"] or 0),
                "min": row["min"],
                "max": row["max"],
                "mean": row["mean"],
                "median": row["median"],
                "std": row["std"],
            }
        )

    return rows


def is_categorical_dtype(dtype: pl.DataType) -> bool:
    return dtype in {pl.Utf8, pl.Categorical, pl.Boolean}


def collect_categorical_top_values(
    lazy_frame: pl.LazyFrame,
    schema: dict[str, pl.DataType],
    distinct_counts: dict[str, int],
    *,
    top_n: int = TOP_VALUE_LIMIT,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for column, dtype in schema.items():
        if not is_categorical_dtype(dtype):
            continue
        if distinct_counts.get(column, 0) > 1_000:
            continue

        top_values = (
            lazy_frame.select(pl.col(column).cast(pl.Utf8).alias("value"))
            .group_by("value")
            .agg(pl.len().alias("count"))
            .sort(["count", "value"], descending=[True, False], nulls_last=True)
            .limit(top_n)
            .collect()
        )

        for rank, item in enumerate(top_values.iter_rows(named=True), start=1):
            rows.append(
                {
                    "column": column,
                    "rank": rank,
                    "value": item["value"],
                    "count": int(item["count"]),
                }
            )

    return rows


def collect_species_counts(
    lazy_frame: pl.LazyFrame,
    schema: dict[str, pl.DataType],
) -> dict[str, int]:
    for column in ("scientificName", "species", "query_scientific_name"):
        if column not in schema:
            continue

        counts = (
            lazy_frame.filter(pl.col(column).is_not_null())
            .group_by(column)
            .agg(pl.len().alias("count"))
            .sort(["count", column], descending=[True, False])
            .collect()
        )
        return {
            str(row[column]): int(row["count"])
            for row in counts.iter_rows(named=True)
        }

    return {}


def duplicate_uuid_count(lazy_frame: pl.LazyFrame, schema: dict[str, pl.DataType]) -> int:
    if "uuid" not in schema:
        return 0

    stats = lazy_frame.select(
        [
            pl.col("uuid").drop_nulls().count().alias("non_null_count"),
            pl.col("uuid").drop_nulls().n_unique().alias("unique_count"),
        ]
    ).collect()
    row = stats.row(0, named=True)
    return int(row["non_null_count"] or 0) - int(row["unique_count"] or 0)


def coordinate_issue_count(lazy_frame: pl.LazyFrame, schema: dict[str, pl.DataType]) -> int:
    if "decimalLatitude" not in schema or "decimalLongitude" not in schema:
        return 0

    latitude_issue = (
        pl.col("decimalLatitude").is_not_null()
        & ((pl.col("decimalLatitude") < -90) | (pl.col("decimalLatitude") > 90))
    )
    longitude_issue = (
        pl.col("decimalLongitude").is_not_null()
        & ((pl.col("decimalLongitude") < -180) | (pl.col("decimalLongitude") > 180))
    )
    stats = lazy_frame.select(
        [
            latitude_issue.sum().alias("latitude_issue_count"),
            longitude_issue.sum().alias("longitude_issue_count"),
        ]
    ).collect()
    row = stats.row(0, named=True)
    return int(row["latitude_issue_count"] or 0) + int(row["longitude_issue_count"] or 0)


def year_coverage(lazy_frame: pl.LazyFrame, schema: dict[str, pl.DataType]) -> dict[str, Any]:
    if "year" not in schema:
        return {}

    stats = lazy_frame.select(
        [
            pl.col("year").drop_nulls().min().alias("min_year"),
            pl.col("year").drop_nulls().max().alias("max_year"),
            pl.col("year").drop_nulls().n_unique().alias("distinct_year_count"),
        ]
    ).collect()
    row = stats.row(0, named=True)
    return {
        "min_year": row["min_year"],
        "max_year": row["max_year"],
        "distinct_year_count": int(row["distinct_year_count"] or 0),
    }


def build_quality_flags(
    *,
    column_profile: list[dict[str, Any]],
    duplicate_uuids: int,
    coordinate_issues: int,
    species_counts: dict[str, int],
) -> list[str]:
    flags: set[str] = set()

    for row in column_profile:
        for flag in str(row.get("flags") or "").split("|"):
            if flag:
                flags.add(flag)

    if duplicate_uuids:
        flags.add("duplicate_uuid")
    if coordinate_issues:
        flags.add("coordinate_range_issue")
    if not species_counts:
        flags.add("missing_species_counts")

    return sorted(flags)


def profile_parquet(path: Path) -> QualityReport:
    parquet_path = Path(path)
    lazy_frame = pl.scan_parquet(parquet_path)
    schema = collect_schema(lazy_frame)
    columns = list(schema)
    row_count = duckdb_row_count(parquet_path)
    null_counts, distinct_counts = collect_column_basics(lazy_frame, columns)
    column_profile = build_column_profile(
        schema=schema,
        row_count=row_count,
        null_counts=null_counts,
        distinct_counts=distinct_counts,
    )
    numeric_stats = collect_numeric_stats(lazy_frame, schema)
    categorical_top_values = collect_categorical_top_values(
        lazy_frame,
        schema,
        distinct_counts,
    )
    species_counts = collect_species_counts(lazy_frame, schema)
    duplicate_uuids = duplicate_uuid_count(lazy_frame, schema)
    coordinate_issues = coordinate_issue_count(lazy_frame, schema)
    years = year_coverage(lazy_frame, schema)
    quality_flags = build_quality_flags(
        column_profile=column_profile,
        duplicate_uuids=duplicate_uuids,
        coordinate_issues=coordinate_issues,
        species_counts=species_counts,
    )

    summary: dict[str, Any] = {
        "profiled_at_utc": utc_timestamp(),
        "parquet_path": str(parquet_path),
        "row_count": row_count,
        "column_count": len(columns),
        "duplicate_uuid_count": duplicate_uuids,
        "coordinate_issue_count": coordinate_issues,
        "species_count": len(species_counts),
        "quality_flags": quality_flags,
    }
    summary.update(years)

    return QualityReport(
        summary=summary,
        column_profile=column_profile,
        numeric_stats=numeric_stats,
        categorical_top_values=categorical_top_values,
        species_counts=species_counts,
        quality_flags=quality_flags,
    )


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_report_files(
    family: FamilyParquet,
    report: QualityReport,
    *,
    output_dir_name: str = DEFAULT_OUTPUT_DIR_NAME,
    notes_filename: str = DEFAULT_NOTES_FILENAME,
) -> ReportOutputs:
    output_dir = family.path.parent / output_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = family.family_key
    outputs = ReportOutputs(
        summary_json=output_dir / f"{prefix}_quality_summary.json",
        column_profile_csv=output_dir / f"{prefix}_column_profile.csv",
        numeric_stats_csv=output_dir / f"{prefix}_numeric_stats.csv",
        categorical_top_values_csv=output_dir / f"{prefix}_categorical_top_values.csv",
        notes_path=output_dir / notes_filename,
    )

    summary_payload = {
        "class_key": family.class_key,
        "order_key": family.order_key,
        "family_key": family.family_key,
        "parquet_path": str(family.path),
        "summary": report.summary,
        "species_counts": report.species_counts,
        "quality_flags": report.quality_flags,
    }
    outputs.summary_json.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    write_csv_rows(
        outputs.column_profile_csv,
        report.column_profile,
        [
            "column",
            "dtype",
            "null_count",
            "null_percent",
            "non_null_count",
            "distinct_count",
            "flags",
        ],
    )
    write_csv_rows(
        outputs.numeric_stats_csv,
        report.numeric_stats,
        ["column", "dtype", "non_null_count", "min", "max", "mean", "median", "std"],
    )
    write_csv_rows(
        outputs.categorical_top_values_csv,
        report.categorical_top_values,
        ["column", "rank", "value", "count"],
    )

    return outputs


def append_family_note(family: FamilyParquet, notes_path: Path, note: str) -> None:
    cleaned = note.strip()
    if not cleaned:
        return

    notes_path.parent.mkdir(parents=True, exist_ok=True)
    with notes_path.open("a", encoding="utf-8") as handle:
        handle.write(f"## {family.family_key} | {utc_timestamp()}\n\n")
        handle.write(f"- class: `{family.class_key}`\n")
        handle.write(f"- order: `{family.order_key}`\n")
        handle.write(f"- parquet: `{family.path}`\n\n")
        handle.write(cleaned + "\n\n")


def print_family_summary(
    family: FamilyParquet,
    report: QualityReport,
    outputs: ReportOutputs,
    *,
    index: int,
    total: int,
) -> None:
    print(f"\n[{index}/{total}] {family.class_key}/{family.order_key}/{family.family_key}")
    print(f"Parquet: {family.path}")
    print(
        "Rows: "
        f"{report.summary['row_count']:,}; "
        f"columns: {report.summary['column_count']:,}; "
        f"species: {report.summary['species_count']:,}; "
        f"duplicate uuids: {report.summary['duplicate_uuid_count']:,}; "
        f"coordinate issues: {report.summary['coordinate_issue_count']:,}"
    )
    if report.quality_flags:
        print("Flags: " + ", ".join(report.quality_flags))
    print(f"Summary JSON: {outputs.summary_json}")
    print(f"Column profile CSV: {outputs.column_profile_csv}")
    print(f"Numeric stats CSV: {outputs.numeric_stats_csv}")
    print(f"Categorical top values CSV: {outputs.categorical_top_values_csv}")


def process_families(
    families: list[FamilyParquet],
    *,
    output_dir_name: str = DEFAULT_OUTPUT_DIR_NAME,
    notes_filename: str = DEFAULT_NOTES_FILENAME,
    interactive: bool = True,
) -> int:
    total = len(families)
    for index, family in enumerate(families, start=1):
        report = profile_parquet(family.path)
        outputs = write_report_files(
            family,
            report,
            output_dir_name=output_dir_name,
            notes_filename=notes_filename,
        )
        print_family_summary(family, report, outputs, index=index, total=total)

        if interactive:
            note = input("Notes for this family, blank to skip: ")
            append_family_note(family, outputs.notes_path, note)

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile family-level ALA Parquet outputs and collect review notes."
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=DEFAULT_DATASET_ROOT,
        help="Root datasets directory. Defaults to datasets/.",
    )
    parser.add_argument(
        "--class",
        dest="taxon_class",
        default=None,
        help="Class folder/facet to process, for example Aves.",
    )
    parser.add_argument(
        "--order",
        default=None,
        help="Order folder/facet to process, for example Psittaciformes.",
    )
    parser.add_argument(
        "--family",
        default=None,
        help="Family folder/facet to process, for example Psittacidae.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR_NAME,
        help="Report directory name created inside each family folder.",
    )
    parser.add_argument(
        "--notes-file",
        default=DEFAULT_NOTES_FILENAME,
        help="Markdown notes filename created inside each report directory.",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Write reports without prompting for reviewer notes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    families = discover_family_parquets(
        args.dataset_root,
        taxon_class=args.taxon_class,
        order=args.order,
        family=args.family,
    )

    if not families:
        print("No family-level parquet files found for the supplied filters.")
        return 1

    return process_families(
        families,
        output_dir_name=args.output_dir,
        notes_filename=args.notes_file,
        interactive=not args.no_interactive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
