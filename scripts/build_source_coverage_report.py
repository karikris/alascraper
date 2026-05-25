#!/usr/bin/env python3.14
"""Compare a state-source occurrence Parquet with the current ALA table."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sources.nsw_bionet import BUTTERFLY_FAMILIES


DEFAULT_ALA_PATH = Path("datasets/insecta/lepidoptera/ala_species_records.parquet")
DEFAULT_SOURCE_PATH = Path(
    "datasets/insecta/lepidoptera/nsw_bionet/nsw_bionet_occurrences.parquet"
)
DEFAULT_REPORT_PATH = Path(
    "datasets/insecta/lepidoptera/nsw_bionet/nsw_bionet_impact_report.json"
)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def add_missing_columns(
    frame: pl.DataFrame,
    columns: dict[str, pl.DataType],
) -> pl.DataFrame:
    out = frame
    for column, dtype in columns.items():
        if column not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=dtype).alias(column))
    return out


def source_match_keys(source: pl.DataFrame, families: tuple[str, ...]) -> pl.Series:
    frame = add_missing_columns(
        source,
        {
            "scientific_name": pl.String,
            "family": pl.String,
            "event_date_start": pl.String,
            "decimal_latitude": pl.Float64,
            "decimal_longitude": pl.Float64,
        },
    )
    if families:
        frame = frame.filter(pl.col("family").is_in(families))
    keyed = frame.select(
        pl.concat_str(
            [
                pl.col("scientific_name").str.to_lowercase().fill_null(""),
                pl.col("event_date_start").fill_null(""),
                pl.col("decimal_latitude").round(5).cast(pl.String).fill_null(""),
                pl.col("decimal_longitude").round(5).cast(pl.String).fill_null(""),
            ],
            separator="|",
        ).alias("match_key")
    )
    return keyed["match_key"]


def ala_match_keys(ala: pl.DataFrame, families: tuple[str, ...]) -> pl.Series:
    frame = add_missing_columns(
        ala,
        {
            "scientificName": pl.String,
            "family": pl.String,
            "eventDate_iso": pl.String,
            "decimalLatitude": pl.Float64,
            "decimalLongitude": pl.Float64,
        },
    )
    if families:
        frame = frame.filter(pl.col("family").is_in(families))
    keyed = frame.select(
        pl.concat_str(
            [
                pl.col("scientificName").str.to_lowercase().fill_null(""),
                pl.col("eventDate_iso").str.slice(0, 10).fill_null(""),
                pl.col("decimalLatitude").round(5).cast(pl.String).fill_null(""),
                pl.col("decimalLongitude").round(5).cast(pl.String).fill_null(""),
            ],
            separator="|",
        ).alias("match_key")
    )
    return keyed["match_key"]


def count_family_rows(path: Path, source_kind: str, families: tuple[str, ...]) -> int:
    if not path.exists():
        return 0

    frame = pl.read_parquet(path)
    family_column = "family"
    if family_column not in frame.columns:
        return frame.height
    if not families:
        return frame.height
    return frame.filter(pl.col(family_column).is_in(families)).height


def family_counts(frame: pl.DataFrame, *, family_column: str = "family") -> dict[str, int]:
    if family_column not in frame.columns or frame.is_empty():
        return {}

    return {
        str(row[family_column]): int(row["count"])
        for row in frame.group_by(family_column)
        .len(name="count")
        .sort(family_column)
        .iter_rows(named=True)
        if row[family_column] is not None
    }


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def ala_family_filter_sql(families: tuple[str, ...], columns: set[str]) -> str:
    if not families or "family" not in columns:
        return ""
    values = ", ".join(sql_string(family) for family in families)
    return f"WHERE {sql_ident('family')} IN ({values})"


def ala_expr(column: str, columns: set[str], fallback: str = "''") -> str:
    if column not in columns:
        return fallback
    return f"CAST({sql_ident(column)} AS VARCHAR)"


def read_ala_summary(ala_path: Path, families: tuple[str, ...]) -> dict[str, Any]:
    parquet_path = sql_string(str(ala_path).replace("\\", "/"))
    con = duckdb.connect(":memory:")
    try:
        columns = {
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet({parquet_path})"
            ).fetchall()
        }
        where_sql = ala_family_filter_sql(families, columns)
        source_sql = f"read_parquet({parquet_path})"
        lat_expr = (
            f"CAST(round(CAST({sql_ident('decimalLatitude')} AS DOUBLE), 5) AS VARCHAR)"
            if "decimalLatitude" in columns
            else "''"
        )
        lon_expr = (
            f"CAST(round(CAST({sql_ident('decimalLongitude')} AS DOUBLE), 5) AS VARCHAR)"
            if "decimalLongitude" in columns
            else "''"
        )
        date_expr = (
            f"substr(CAST({sql_ident('eventDate_iso')} AS VARCHAR), 1, 10)"
            if "eventDate_iso" in columns
            else "''"
        )
        name_expr = ala_expr("scientificName", columns)
        key_sql = f"""
            SELECT
                lower(coalesce({name_expr}, '')) || '|' ||
                coalesce({date_expr}, '') || '|' ||
                coalesce({lat_expr}, '') || '|' ||
                coalesce({lon_expr}, '') AS match_key
            FROM {source_sql}
            {where_sql}
        """
        count_sql = f"SELECT count(*) FROM {source_sql} {where_sql}"
        row_count = int(con.execute(count_sql).fetchone()[0])
        keys = {row[0] for row in con.execute(key_sql).fetchall()}

        counts: dict[str, int] = {}
        if "family" in columns:
            for family, count in con.execute(
                f"""
                SELECT {sql_ident('family')}, count(*)
                FROM {source_sql}
                {where_sql}
                GROUP BY {sql_ident('family')}
                ORDER BY {sql_ident('family')}
                """
            ).fetchall():
                if family is not None:
                    counts[str(family)] = int(count)

        return {
            "row_count": row_count,
            "keys": keys,
            "family_counts": counts,
        }
    finally:
        con.close()


def build_impact_report(
    *,
    ala_path: Path = DEFAULT_ALA_PATH,
    source_path: Path = DEFAULT_SOURCE_PATH,
    output_path: Path | None = DEFAULT_REPORT_PATH,
    source_name: str = "nsw_bionet",
    families: tuple[str, ...] = BUTTERFLY_FAMILIES,
) -> dict[str, Any]:
    source = pl.read_parquet(source_path)
    if families and "family" in source.columns:
        source_considered = source.filter(pl.col("family").is_in(families))
    else:
        source_considered = source

    source_keys = source_match_keys(source_considered, families)
    source_rows = source_considered.height
    ala_table_found = ala_path.exists()
    ala_table_readable = False
    ala_read_error: str | None = None

    if ala_table_found:
        try:
            ala_summary = read_ala_summary(ala_path, families)
        except BaseException as exc:
            ala_read_error = str(exc)
            ala_keys = set()
            existing_ala_rows_considered = 0
            ala_counts: dict[str, int] = {}
        else:
            ala_table_readable = True
            ala_keys = ala_summary["keys"]
            existing_ala_rows_considered = ala_summary["row_count"]
            ala_counts = ala_summary["family_counts"]
    else:
        ala_keys = set()
        existing_ala_rows_considered = 0
        ala_counts = {}

    duplicate_flags = [key in ala_keys for key in source_keys.to_list()]
    candidate_duplicate_rows = sum(1 for flag in duplicate_flags if flag)
    candidate_new_rows = source_rows - candidate_duplicate_rows
    expected_harmonised_rows_without_dedupe = existing_ala_rows_considered + source_rows
    expected_harmonised_rows_after_candidate_dedupe = (
        existing_ala_rows_considered + candidate_new_rows
    )

    impact: dict[str, Any] = {
        "built_at_utc": utc_timestamp(),
        "source_name": source_name,
        "source_path": str(source_path),
        "ala_path": str(ala_path),
        "ala_table_found": ala_table_found,
        "ala_table_readable": ala_table_readable,
        "ala_read_error": ala_read_error,
        "families": list(families),
        "existing_ala_rows_considered": existing_ala_rows_considered,
        "source_rows": source_rows,
        "candidate_duplicate_rows": candidate_duplicate_rows,
        "candidate_new_rows": candidate_new_rows,
        "candidate_duplicate_ratio": (
            round(candidate_duplicate_rows / source_rows, 6) if source_rows else 0.0
        ),
        "expected_harmonised_rows_without_dedupe": expected_harmonised_rows_without_dedupe,
        "expected_harmonised_rows_after_candidate_dedupe": (
            expected_harmonised_rows_after_candidate_dedupe
        ),
        "existing_ala_table_changed": False,
        "source_family_counts": family_counts(source_considered),
        "ala_family_counts": ala_counts,
        "match_basis": "scientific name + event date + rounded latitude/longitude",
        "expected_effect": (
            f"Adding {source_name} does not mutate ala_species_records.parquet; "
            f"it would add {candidate_new_rows:,} candidate-new source rows to a "
            "harmonised occurrence table and mark "
            f"{candidate_duplicate_rows:,} source rows as candidate duplicates for review."
        ),
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(impact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return impact


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a state-source impact report against the current ALA Parquet."
    )
    parser.add_argument("--ala", type=Path, default=DEFAULT_ALA_PATH)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--source-name", default="nsw_bionet")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    impact = build_impact_report(
        ala_path=args.ala,
        source_path=args.source,
        output_path=args.output,
        source_name=args.source_name,
    )
    print(impact["expected_effect"])
    print(f"Wrote impact report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
