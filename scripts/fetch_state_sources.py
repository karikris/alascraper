#!/usr/bin/env python3.14
"""Fetch public state-source occurrence records into source-specific Parquet."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_source_coverage_report import build_impact_report
from sources.nsw_bionet import (
    BUTTERFLY_FAMILIES,
    DEFAULT_PAGE_SIZE,
    NSWBioNetAdapter,
)


DEFAULT_OUTPUT_ROOT = Path("datasets/insecta/lepidoptera")
DEFAULT_ALA_PARQUET = DEFAULT_OUTPUT_ROOT / "ala_species_records.parquet"


def nsw_output_paths(output_root: Path) -> tuple[Path, Path, Path]:
    source_root = output_root / "nsw_bionet"
    return (
        source_root / "nsw_bionet_occurrences.parquet",
        source_root / "metadata.json",
        source_root / "nsw_bionet_impact_report.json",
    )


def fetch_nsw_bionet(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    ala_parquet: Path = DEFAULT_ALA_PARQUET,
    page_size: int = DEFAULT_PAGE_SIZE,
    limit: int | None = None,
) -> int:
    output_path, metadata_path, report_path = nsw_output_paths(output_root)
    adapter = NSWBioNetAdapter()
    result = adapter.fetch_occurrences(
        output_path=output_path,
        metadata_path=metadata_path,
        families=BUTTERFLY_FAMILIES,
        page_size=page_size,
        max_records=limit,
    )
    impact = build_impact_report(
        ala_path=ala_parquet,
        source_path=result.output_path,
        output_path=report_path,
        source_name=result.source,
        families=BUTTERFLY_FAMILIES,
    )

    print(f"Wrote NSW BioNet Parquet: {result.output_path}")
    print(f"Wrote NSW BioNet metadata: {result.metadata_path}")
    print(f"Wrote NSW BioNet impact report: {report_path}")
    print(impact["expected_effect"])
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch public state biodiversity sources for butterfly occurrences."
    )
    parser.add_argument(
        "--source",
        choices=["nsw_bionet"],
        default="nsw_bionet",
        help="State source adapter to run. NSW BioNet is the first public adapter.",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--ala-parquet", type=Path, default=DEFAULT_ALA_PARQUET)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max records for smoke tests. Omit for the full public source fetch.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.source == "nsw_bionet":
        return fetch_nsw_bionet(
            output_root=args.output_root,
            ala_parquet=args.ala_parquet,
            page_size=args.page_size,
            limit=args.limit,
        )
    raise ValueError(f"Unsupported source: {args.source}")


if __name__ == "__main__":
    raise SystemExit(main())
