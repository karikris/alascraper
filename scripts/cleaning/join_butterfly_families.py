#!/usr/bin/env python3.14
"""
Join the six Australian butterfly family Parquet outputs into one dataset.

Default input:
    datasets/insecta/lepidoptera/<family>/<family>.parquet

Default output:
    datasets/insecta/lepidoptera/butterflies.parquet
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import alascraper as a
from scripts.cleaning import profile_family_parquet as profiler


DEFAULT_DATASET_ROOT = Path("datasets")
DEFAULT_DATASET_CLASS = "insecta"
DEFAULT_ORDER = "Lepidoptera"
DEFAULT_OUTPUT_NAME = "butterflies"
DEFAULT_REPORT_DIR = "quality_reports"


@dataclass(frozen=True)
class JoinOutputs:
    output_parquet: Path
    metadata_json: Path
    summary_json: Path
    column_profile_csv: Path
    categorical_top_values_csv: Path


@dataclass(frozen=True)
class JoinStats:
    input_rows: int
    output_rows: int
    dropped_rows: int
    per_family_input_rows: dict[str, int]


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def file_list_sql(paths: list[Path]) -> str:
    return "[" + ",".join(sql_string(path.as_posix()) for path in paths) + "]"


def order_root(dataset_root: Path, dataset_class: str, order: str) -> Path:
    return Path(dataset_root) / a.safe_key(dataset_class) / a.safe_key(order)


def butterfly_family_inputs(
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    dataset_class: str = DEFAULT_DATASET_CLASS,
    order: str = DEFAULT_ORDER,
) -> list[Path]:
    root = order_root(dataset_root, dataset_class, order)
    return [
        root / a.safe_key(family) / f"{a.safe_key(family)}.parquet"
        for family in a.BUTTERFLY_FAMILIES
    ]


def output_paths(
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    dataset_class: str = DEFAULT_DATASET_CLASS,
    order: str = DEFAULT_ORDER,
    output_name: str = DEFAULT_OUTPUT_NAME,
) -> JoinOutputs:
    root = order_root(dataset_root, dataset_class, order)
    output_key = a.safe_key(output_name)
    report_root = root / DEFAULT_REPORT_DIR
    return JoinOutputs(
        output_parquet=root / f"{output_key}.parquet",
        metadata_json=root / f"{output_key}_metadata.json",
        summary_json=report_root / f"{output_key}_quality_summary.json",
        column_profile_csv=report_root / f"{output_key}_column_profile.csv",
        categorical_top_values_csv=report_root / f"{output_key}_categorical_top_values.csv",
    )


def validate_inputs(input_paths: list[Path]) -> None:
    missing = [path for path in input_paths if not path.exists()]
    if missing:
        missing_text = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(
            f"Missing required butterfly family parquet files:\n{missing_text}"
        )


def ensure_can_write(outputs: JoinOutputs, *, overwrite: bool) -> None:
    existing = [
        path
        for path in (
            outputs.output_parquet,
            outputs.metadata_json,
            outputs.summary_json,
            outputs.column_profile_csv,
            outputs.categorical_top_values_csv,
        )
        if path.exists()
    ]
    if existing and not overwrite:
        existing_text = "\n".join(f"- {path}" for path in existing)
        raise FileExistsError(
            "Output files already exist; rerun with --overwrite to replace them:\n"
            f"{existing_text}"
        )


def remove_existing_outputs(outputs: JoinOutputs) -> None:
    for path in (
        outputs.output_parquet,
        outputs.metadata_json,
        outputs.summary_json,
        outputs.column_profile_csv,
        outputs.categorical_top_values_csv,
    ):
        path.unlink(missing_ok=True)


def join_parquets(input_paths: list[Path], output_path: Path) -> JoinStats:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")

    try:
        threads = min(a.WORKERS, os.cpu_count() or a.WORKERS)
        con.execute(f"PRAGMA threads={threads}")
        per_family_rows = {
            path.parent.name: int(
                con.execute(
                    f"SELECT count(*) FROM read_parquet({sql_string(path.as_posix())})"
                ).fetchone()[0]
            )
            for path in input_paths
        }
        source_sql = f"read_parquet({file_list_sql(input_paths)}, union_by_name=true)"
        select_sql = a.deduped_select_sql(source_sql)
        input_rows = int(con.execute(f"SELECT count(*) FROM {source_sql}").fetchone()[0])
        output_rows = int(
            con.execute(f"SELECT count(*) FROM ({select_sql})").fetchone()[0]
        )

        con.execute(
            f"""
            COPY (
                {select_sql}
            )
            TO {sql_string(output_path.as_posix())}
            (
                FORMAT parquet,
                COMPRESSION '{a.PARQUET_COMPRESSION}',
                ROW_GROUP_SIZE {a.PARQUET_ROW_GROUP_SIZE}
            )
            """
        )
    finally:
        con.close()

    return JoinStats(
        input_rows=input_rows,
        output_rows=output_rows,
        dropped_rows=input_rows - output_rows,
        per_family_input_rows=per_family_rows,
    )


def write_metadata(
    *,
    outputs: JoinOutputs,
    input_paths: list[Path],
    stats: JoinStats,
    dataset_class: str,
    order: str,
    output_name: str,
    started_at_utc: str,
) -> None:
    payload: dict[str, Any] = {
        "run_started_utc": started_at_utc,
        "run_finished_utc": utc_timestamp(),
        "dataset_class": a.safe_key(dataset_class),
        "order": order,
        "dataset_key": a.safe_key(output_name),
        "source_family_keys": [
            a.safe_key(family) for family in a.BUTTERFLY_FAMILIES
        ],
        "input_files": [str(path) for path in input_paths],
        "output_parquet_filename": outputs.output_parquet.name,
        "input_rows": stats.input_rows,
        "output_rows": stats.output_rows,
        "dropped_rows": stats.dropped_rows,
        "per_family_input_rows": stats.per_family_input_rows,
        "dedupe_by_uuid_or_fingerprint": a.DEDUPE_BY_UUID,
    }
    outputs.metadata_json.parent.mkdir(parents=True, exist_ok=True)
    outputs.metadata_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_quality_reports(
    *,
    outputs: JoinOutputs,
    input_paths: list[Path],
    dataset_class: str,
    order: str,
    output_name: str,
) -> None:
    report = profiler.profile_parquet(outputs.output_parquet)
    report_root = outputs.summary_json.parent
    report_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "class_key": a.safe_key(dataset_class),
        "order_key": a.safe_key(order),
        "dataset_key": a.safe_key(output_name),
        "source_family_keys": [
            a.safe_key(family) for family in a.BUTTERFLY_FAMILIES
        ],
        "input_files": [str(path) for path in input_paths],
        "parquet_path": str(outputs.output_parquet),
        "summary": report.summary,
        "species_counts": report.species_counts,
        "quality_flags": report.quality_flags,
    }
    outputs.summary_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    profiler.write_csv_rows(
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
    profiler.write_csv_rows(
        outputs.categorical_top_values_csv,
        report.categorical_top_values,
        ["column", "rank", "value", "count"],
    )


def join_butterfly_families(
    *,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    dataset_class: str = DEFAULT_DATASET_CLASS,
    order: str = DEFAULT_ORDER,
    output_name: str = DEFAULT_OUTPUT_NAME,
    overwrite: bool = False,
) -> JoinOutputs:
    started_at_utc = utc_timestamp()
    input_paths = butterfly_family_inputs(dataset_root, dataset_class, order)
    outputs = output_paths(dataset_root, dataset_class, order, output_name)
    validate_inputs(input_paths)
    ensure_can_write(outputs, overwrite=overwrite)
    remove_existing_outputs(outputs)

    stats = join_parquets(input_paths, outputs.output_parquet)
    write_metadata(
        outputs=outputs,
        input_paths=input_paths,
        stats=stats,
        dataset_class=dataset_class,
        order=order,
        output_name=output_name,
        started_at_utc=started_at_utc,
    )
    write_quality_reports(
        outputs=outputs,
        input_paths=input_paths,
        dataset_class=dataset_class,
        order=order,
        output_name=output_name,
    )
    return outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join six butterfly family Parquet files into one dataset."
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=DEFAULT_DATASET_ROOT,
        help="Root datasets directory. Defaults to datasets/.",
    )
    parser.add_argument(
        "--dataset-class",
        default=DEFAULT_DATASET_CLASS,
        help="Dataset class folder. Defaults to insecta.",
    )
    parser.add_argument(
        "--order",
        default=DEFAULT_ORDER,
        help="Order folder/facet. Defaults to Lepidoptera.",
    )
    parser.add_argument(
        "--output-name",
        default=DEFAULT_OUTPUT_NAME,
        help="Output filename stem. Defaults to butterflies.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing joined parquet, metadata, and joined quality reports.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        outputs = join_butterfly_families(
            dataset_root=args.dataset_root,
            dataset_class=args.dataset_class,
            order=args.order,
            output_name=args.output_name,
            overwrite=args.overwrite,
        )
    except (FileExistsError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Wrote joined butterfly Parquet: {outputs.output_parquet}")
    print(f"Wrote metadata: {outputs.metadata_json}")
    print(f"Wrote quality summary: {outputs.summary_json}")
    print(f"Wrote column profile: {outputs.column_profile_csv}")
    print(f"Wrote categorical top values: {outputs.categorical_top_values_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
