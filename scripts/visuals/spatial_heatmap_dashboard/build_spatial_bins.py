#!/usr/bin/env python3.14
"""Build spatial aggregate Parquet tables for the butterfly dashboard."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl


DEFAULT_SOURCE_PATH = Path("datasets/insecta/lepidoptera/butterflies_cleaned.parquet")
DEFAULT_OUTPUT_DIR = Path("datasets/insecta/lepidoptera/dashboard")
DEFAULT_GRID_DECIMALS = 2


@dataclass(frozen=True)
class SpatialBinOutputs:
    grid_bins: Path
    h3_bins: Path | None
    dimensions_json: Path


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def spatial_source(lazy_frame: pl.LazyFrame, grid_decimals: int) -> pl.LazyFrame:
    return (
        lazy_frame.filter(
            pl.col("decimalLatitude").is_not_null()
            & pl.col("decimalLongitude").is_not_null()
        )
        .with_columns(
            [
                pl.col("decimalLatitude").round(grid_decimals).alias("lat_bin"),
                pl.col("decimalLongitude").round(grid_decimals).alias("lon_bin"),
            ]
        )
    )


def grid_aggregates(lazy_frame: pl.LazyFrame, grid_decimals: int) -> pl.DataFrame:
    grouped_columns = [
        "lat_bin",
        "lon_bin",
        "family",
        "genus",
        "species",
        "scientificName",
        "year",
        "stateProvince",
    ]
    return (
        spatial_source(lazy_frame, grid_decimals)
        .group_by(grouped_columns)
        .agg(
            [
                pl.len().alias("record_count"),
                pl.col("scientificName").n_unique().alias("distinct_scientific_names"),
                pl.col("taxonConceptID").n_unique().alias("distinct_taxon_concepts"),
                pl.col("year").min().alias("min_year"),
                pl.col("year").max().alias("max_year"),
            ]
        )
        .sort(grouped_columns, nulls_last=True)
        .collect()
    )


def dimension_values(lazy_frame: pl.LazyFrame, mapped_row_count: int) -> dict[str, Any]:
    row = lazy_frame.select(
        [
            pl.len().alias("row_count"),
            pl.col("year").drop_nulls().min().alias("min_year"),
            pl.col("year").drop_nulls().max().alias("max_year"),
        ]
    ).collect().row(0, named=True)
    family_values = lazy_frame.select(
        pl.col("family").drop_nulls().unique().sort()
    ).collect().to_series().to_list()
    species_values = lazy_frame.select(
        pl.col("species").drop_nulls().unique().sort()
    ).collect().to_series().to_list()
    genus_values = lazy_frame.select(
        pl.col("genus").drop_nulls().unique().sort()
    ).collect().to_series().to_list()
    scientific_name_values = lazy_frame.select(
        pl.col("scientificName").drop_nulls().unique().sort()
    ).collect().to_series().to_list()
    state_values = lazy_frame.select(
        pl.col("stateProvince").drop_nulls().unique().sort()
    ).collect().to_series().to_list()
    year_values = lazy_frame.select(
        pl.col("year").drop_nulls().unique().sort()
    ).collect().to_series().to_list()
    return {
        "built_at_utc": utc_timestamp(),
        "row_count": int(row["row_count"]),
        "mapped_row_count": mapped_row_count,
        "family_values": family_values,
        "genus_values": genus_values,
        "species_values": species_values,
        "scientific_name_values": scientific_name_values,
        "state_values": state_values,
        "year_values": year_values,
        "min_year": row["min_year"],
        "max_year": row["max_year"],
        "canonical_species_column": "species",
    }


def build_spatial_bins(
    *,
    source_path: Path = DEFAULT_SOURCE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    grid_decimals: int = DEFAULT_GRID_DECIMALS,
    h3_resolution: int | None = None,
) -> SpatialBinOutputs:
    source = Path(source_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lazy_frame = pl.scan_parquet(source)
    grid = grid_aggregates(lazy_frame, grid_decimals)
    grid_path = out_dir / "butterfly_grid_bins.parquet"
    grid.write_parquet(grid_path)

    h3_path: Path | None = None
    if h3_resolution is not None:
        h3_path = out_dir / "butterfly_h3_bins.parquet"
        # H3 support is intentionally optional. The file records that the
        # resolution was requested but h3-python is not required for core tests.
        pl.DataFrame(
            {
                "h3_resolution": [h3_resolution],
                "status": ["h3 dependency not enabled in this environment"],
            }
        ).write_parquet(h3_path)

    dimensions_path = out_dir / "dashboard_dimensions.json"
    dimensions = dimension_values(lazy_frame, mapped_row_count=grid["record_count"].sum())
    dimensions["source_path"] = str(source)
    dimensions["grid_decimals"] = grid_decimals
    dimensions["grid_bins_path"] = str(grid_path)
    dimensions["h3_bins_path"] = str(h3_path) if h3_path else None
    dimensions_path.write_text(
        json.dumps(dimensions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return SpatialBinOutputs(
        grid_bins=grid_path,
        h3_bins=h3_path,
        dimensions_json=dimensions_path,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build rounded-coordinate spatial aggregate bins for dashboard maps."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--grid-decimals", type=int, default=DEFAULT_GRID_DECIMALS)
    parser.add_argument("--h3-resolution", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = build_spatial_bins(
        source_path=args.source,
        output_dir=args.output_dir,
        grid_decimals=args.grid_decimals,
        h3_resolution=args.h3_resolution,
    )
    print(f"Wrote grid bins: {outputs.grid_bins}")
    if outputs.h3_bins:
        print(f"Wrote H3 placeholder bins: {outputs.h3_bins}")
    print(f"Wrote dashboard dimensions: {outputs.dimensions_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
