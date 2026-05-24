#!/usr/bin/env python3.14
"""
Complete nullable genus/species fields for butterfly occurrence datasets.

The script treats the `species` column as the canonical species-level field for
dashboard slicers. It first fills null genus/species values from unambiguous
complete records with the same scientificName, then optionally attempts guarded
external lookup for unresolved names.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import polars as pl
import requests


DEFAULT_SOURCE_PATH = Path("datasets/insecta/lepidoptera/butterflies.parquet")
DEFAULT_OUTPUT_PATH = Path("datasets/insecta/lepidoptera/butterflies_cleaned.parquet")
DEFAULT_REPORT_DIR = Path("datasets/insecta/lepidoptera/quality_reports")
SPECIES_LEVEL_RANKS = {"species", "subspecies"}
ALA_SPECIES_AUTO_URL = "https://api.ala.org.au/species/search/auto"
GBIF_SPECIES_MATCH_URL = "https://api.gbif.org/v1/species/match"


@dataclass(frozen=True)
class TaxonomyLookupResult:
    source: str
    rank: str | None
    genus: str | None
    species: str | None
    accepted: bool


@dataclass(frozen=True)
class TaxonomyCompletionOutputs:
    output_parquet: Path
    report_json: Path
    missing_rows_csv: Path
    month_counts_csv: Path


ExternalLookup = Callable[[str], TaxonomyLookupResult | None]


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def report_paths(report_dir: Path) -> tuple[Path, Path, Path]:
    return (
        report_dir / "butterflies_taxonomy_fill_report.json",
        report_dir / "butterflies_missing_taxonomy_rows.csv",
        report_dir / "butterflies_month_counts.csv",
    )


def write_csv_rows(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalise_rank(rank: str | None) -> str:
    return (rank or "").strip().lower()


def is_accepted_species_lookup(result: TaxonomyLookupResult | None) -> bool:
    if result is None:
        return False
    return (
        result.accepted
        and normalise_rank(result.rank) in SPECIES_LEVEL_RANKS
        and bool(result.genus)
        and bool(result.species)
    )


def species_binomial_from_name(name: str | None) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    raw_parts = [
        part for part in name.replace("(", " ").replace(")", " ").split() if part
    ]
    parts = [
        part
        for index, part in enumerate(raw_parts)
        if not (index > 0 and part[:1].isupper() and len(part) > 1)
    ]
    if len(parts) < 2:
        return parts[0] if parts else None, None
    return parts[0], f"{parts[0]} {parts[1]}"


def exact_lookup_frame(df: pl.DataFrame) -> pl.DataFrame:
    complete = df.filter(
        pl.col("scientificName").is_not_null()
        & pl.col("genus").is_not_null()
        & pl.col("species").is_not_null()
    )
    if complete.is_empty():
        return pl.DataFrame(
            schema={
                "scientificName": pl.Utf8,
                "lookup_genus": pl.Utf8,
                "lookup_species": pl.Utf8,
            }
        )

    grouped = complete.group_by("scientificName").agg(
        [
            pl.col("genus").n_unique().alias("genus_options"),
            pl.col("species").n_unique().alias("species_options"),
            pl.col("genus").drop_nulls().first().alias("lookup_genus"),
            pl.col("species").drop_nulls().first().alias("lookup_species"),
        ]
    )
    return grouped.filter(
        (pl.col("genus_options") == 1) & (pl.col("species_options") == 1)
    ).select(["scientificName", "lookup_genus", "lookup_species"])


def apply_exact_fills(df: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    before_missing = df.filter(pl.col("genus").is_null() | pl.col("species").is_null()).height
    lookup = exact_lookup_frame(df)
    if lookup.is_empty():
        return df, 0

    filled = (
        df.join(lookup, on="scientificName", how="left")
        .with_columns(
            [
                pl.coalesce([pl.col("genus"), pl.col("lookup_genus")]).alias("genus"),
                pl.coalesce([pl.col("species"), pl.col("lookup_species")]).alias("species"),
            ]
        )
        .drop(["lookup_genus", "lookup_species"])
    )
    after_missing = filled.filter(
        pl.col("genus").is_null() | pl.col("species").is_null()
    ).height
    return filled, before_missing - after_missing


def unresolved_scientific_names(df: pl.DataFrame) -> list[str]:
    return (
        df.filter(
            (pl.col("genus").is_null() | pl.col("species").is_null())
            & pl.col("scientificName").is_not_null()
        )
        .select("scientificName")
        .unique()
        .sort("scientificName")
        .to_series()
        .to_list()
    )


def apply_external_fills(
    df: pl.DataFrame,
    external_lookup: ExternalLookup | None,
) -> tuple[pl.DataFrame, int, int, list[dict[str, object]]]:
    if external_lookup is None:
        return df, 0, 0, []

    lookup_rows: list[dict[str, object]] = []
    rejected = 0
    for scientific_name in unresolved_scientific_names(df):
        result = external_lookup(scientific_name)
        if not is_accepted_species_lookup(result):
            rejected += 1
            continue
        assert result is not None
        lookup_rows.append(
            {
                "scientificName": scientific_name,
                "external_genus": result.genus,
                "external_species": result.species,
                "external_source": result.source,
                "external_rank": result.rank,
            }
        )

    if not lookup_rows:
        return df, 0, rejected, []

    lookup = pl.DataFrame(lookup_rows)
    before_missing = df.filter(pl.col("genus").is_null() | pl.col("species").is_null()).height
    filled = (
        df.join(lookup, on="scientificName", how="left")
        .with_columns(
            [
                pl.coalesce([pl.col("genus"), pl.col("external_genus")]).alias("genus"),
                pl.coalesce([pl.col("species"), pl.col("external_species")]).alias("species"),
            ]
        )
        .drop(["external_genus", "external_species", "external_source", "external_rank"])
    )
    after_missing = filled.filter(
        pl.col("genus").is_null() | pl.col("species").is_null()
    ).height
    return filled, before_missing - after_missing, rejected, lookup_rows


def ala_species_lookup(scientific_name: str) -> TaxonomyLookupResult | None:
    response = requests.get(
        ALA_SPECIES_AUTO_URL,
        params={"q": scientific_name, "idxType": "TAXON", "limit": 5},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("autoCompleteList") or []
    if len(candidates) != 1:
        exact_candidates = [
            item for item in candidates
            if str(item.get("name") or "").casefold() == scientific_name.casefold()
        ]
        if len(exact_candidates) != 1:
            return None
        candidate = exact_candidates[0]
    else:
        candidate = candidates[0]

    rank = candidate.get("rankString")
    genus, species = species_binomial_from_name(candidate.get("name"))
    return TaxonomyLookupResult(
        source="ala",
        rank=rank,
        genus=genus,
        species=species,
        accepted=True,
    )


def gbif_species_match_lookup(scientific_name: str) -> TaxonomyLookupResult | None:
    response = requests.get(
        GBIF_SPECIES_MATCH_URL,
        params={"name": scientific_name},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    rank = data.get("rank")
    status = str(data.get("status") or "").upper()
    confidence = int(data.get("confidence") or 0)
    accepted = status in {"ACCEPTED", "SYNONYM"} and confidence >= 90
    return TaxonomyLookupResult(
        source="gbif",
        rank=rank,
        genus=data.get("genus"),
        species=data.get("species"),
        accepted=accepted,
    )


def default_external_lookup(scientific_name: str) -> TaxonomyLookupResult | None:
    for lookup in (ala_species_lookup, gbif_species_match_lookup):
        try:
            result = lookup(scientific_name)
        except requests.RequestException:
            continue
        if result is not None:
            return result
    return None


def month_count_rows(df: pl.DataFrame) -> list[dict[str, object]]:
    return [
        {"month": row["month"], "rows": row["rows"]}
        for row in df.group_by("month")
        .agg(pl.len().alias("rows"))
        .sort("month", nulls_last=True)
        .iter_rows(named=True)
    ]


def missing_taxonomy_rows(df: pl.DataFrame) -> list[dict[str, object]]:
    columns = [
        "uuid",
        "scientificName",
        "taxonRank",
        "genus",
        "species",
        "family",
        "taxonConceptID",
        "month",
        "year",
    ]
    available = [column for column in columns if column in df.columns]
    return (
        df.filter(pl.col("genus").is_null() | pl.col("species").is_null())
        .select(available)
        .sort(["scientificName", "uuid"], nulls_last=True)
        .to_dicts()
    )


def complete_taxonomy_fields(
    *,
    source_path: Path = DEFAULT_SOURCE_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    external_lookup: ExternalLookup | None = default_external_lookup,
) -> TaxonomyCompletionOutputs:
    started_at = utc_timestamp()
    source = Path(source_path)
    output = Path(output_path)
    report_json, missing_rows_csv, month_counts_csv = report_paths(Path(report_dir))
    df = pl.read_parquet(source)
    input_rows = df.height
    initial_missing = df.filter(pl.col("genus").is_null() | pl.col("species").is_null()).height

    exact_filled_df, exact_fill_rows = apply_exact_fills(df)
    externally_filled_df, external_fill_rows, rejected_count, external_lookup_rows = (
        apply_external_fills(exact_filled_df, external_lookup)
    )
    remaining_missing = externally_filled_df.filter(
        pl.col("genus").is_null() | pl.col("species").is_null()
    ).height

    output.parent.mkdir(parents=True, exist_ok=True)
    externally_filled_df.write_parquet(output)

    missing_rows = missing_taxonomy_rows(externally_filled_df)
    month_rows = month_count_rows(externally_filled_df)
    write_csv_rows(
        missing_rows_csv,
        missing_rows,
        [
            "uuid",
            "scientificName",
            "taxonRank",
            "genus",
            "species",
            "family",
            "taxonConceptID",
            "month",
            "year",
        ],
    )
    write_csv_rows(month_counts_csv, month_rows, ["month", "rows"])

    payload = {
        "run_started_utc": started_at,
        "run_finished_utc": utc_timestamp(),
        "source_path": str(source),
        "output_path": str(output),
        "input_rows": input_rows,
        "output_rows": externally_filled_df.height,
        "initial_missing_rows": initial_missing,
        "exact_fill_rows": exact_fill_rows,
        "external_fill_rows": external_fill_rows,
        "remaining_missing_rows": remaining_missing,
        "rejected_external_lookup_count": rejected_count,
        "external_lookup_rows": external_lookup_rows,
        "canonical_species_column": "species",
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return TaxonomyCompletionOutputs(
        output_parquet=output,
        report_json=report_json,
        missing_rows_csv=missing_rows_csv,
        month_counts_csv=month_counts_csv,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill unambiguous null genus/species values and report unresolved rows."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--no-external-lookup",
        action="store_true",
        help="Disable GBIF fallback and use only exact in-dataset fills.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = complete_taxonomy_fields(
        source_path=args.source,
        output_path=args.output,
        report_dir=args.report_dir,
        external_lookup=None if args.no_external_lookup else default_external_lookup,
    )
    print(f"Wrote cleaned parquet: {outputs.output_parquet}")
    print(f"Wrote taxonomy fill report: {outputs.report_json}")
    print(f"Wrote unresolved taxonomy rows: {outputs.missing_rows_csv}")
    print(f"Wrote month counts: {outputs.month_counts_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
