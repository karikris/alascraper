#!/usr/bin/env python3.14
"""
Fetch scientific names of Australian taxa for one order from ALA occurrence facets.

Output:
  datasets/<class_or_misc>/<order>/<order_key>_species.csv
  datasets/<class_or_misc>/<order>/<order_key>_species.json

This creates an occurrence-backed ALA list for Australia-only records, not a
static taxonomic monograph. Validate final taxonomy against GBIF and relevant
taxon-specific authorities for publication workflows.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from constants import (
    API_URL,
    COUNTRY_FILTER,
    DEFAULT_GENERATED_ORDER,
    INVALID_TAXON_LABELS,
    QUALITY_CONTROL,
    QUALITY_PROFILE,
    TARGET_GENERATOR_FACET_FIELDS,
    TARGET_GENERATOR_FACET_LIMIT,
    TARGET_GENERATOR_OUTPUT_DIR,
    TARGET_GENERATOR_REQUEST_SLEEP_SECONDS,
    TARGET_GENERATOR_TIMEOUT_SECONDS,
    TARGET_GENERATOR_USER_AGENT,
)


# =============================================================================
# USER CONSTANTS
# =============================================================================

ORDER = DEFAULT_GENERATED_ORDER
OUTPUT_DIR = TARGET_GENERATOR_OUTPUT_DIR

# `species` is the stable baseline. `subspecies` is included where ALA facets
# expose lower taxa, supporting the 596 species/subspecies target.
FACET_FIELDS = TARGET_GENERATOR_FACET_FIELDS
FACET_LIMIT = TARGET_GENERATOR_FACET_LIMIT
REQUEST_SLEEP_SECONDS = TARGET_GENERATOR_REQUEST_SLEEP_SECONDS
TIMEOUT_SECONDS = TARGET_GENERATOR_TIMEOUT_SECONDS
USER_AGENT = TARGET_GENERATOR_USER_AGENT


# =============================================================================
# HELPERS
# =============================================================================

def safe_key(scientific_name: str) -> str:
    text = scientific_name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def output_paths(order: str, output_dir: Path = OUTPUT_DIR) -> tuple[Path, Path]:
    order_key = safe_key(order)
    return (
        output_dir / f"{order_key}_species.csv",
        output_dir / f"{order_key}_species.json",
    )


def is_probable_scientific_name(name: str) -> bool:
    """
    Keep species/subspecies-like names; exclude genus/family/order labels.
    Allows names like `Papilio aegeus` and `Papilio aegeus aegeus`.
    """
    text = name.strip()
    parts = text.split()

    if len(parts) < 2:
        return False

    if text.lower() in INVALID_TAXON_LABELS:
        return False

    if parts[0].lower() in {"not", "unknown", "unidentified"}:
        return False

    if not re.match(r"^[A-Z][a-zA-Z-]+$", parts[0]):
        return False

    return all(re.match(r"^[a-z][a-z-]+$", part) for part in parts[1:])


def build_params(
    order: str,
    facet_field: str,
    facet_offset: int = 0,
) -> list[tuple[str, str | int]]:
    return [
        ("q", "*:*"),
        ("fq", COUNTRY_FILTER),
        ("fq", f'order:"{order}"'),
        ("qualityProfile", QUALITY_PROFILE),
        ("qc", QUALITY_CONTROL),
        ("pageSize", 0),
        ("facet", "true"),
        ("facets", facet_field),
        ("flimit", FACET_LIMIT),
        ("foffset", facet_offset),
    ]


def fetch_order_taxa(
    session: requests.Session,
    order: str,
    facet_field: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    facet_offset = 0

    while True:
        response = session.get(
            API_URL,
            params=build_params(order, facet_field, facet_offset),
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        page_items: list[dict[str, Any]] = []

        for facet in data.get("facetResults", []):
            if facet.get("fieldName") != facet_field:
                continue

            page_items.extend(facet.get("fieldResult", []))

        for item in page_items:
            scientific_name = item.get("label")
            count = int(item.get("count", 0) or 0)
            fq = item.get("fq")

            if scientific_name and count > 0 and is_probable_scientific_name(scientific_name):
                rows.append(
                    {
                        "species_key": safe_key(scientific_name),
                        "scientific_name": scientific_name,
                        "order": order,
                        "facet_field": facet_field,
                        "ala_occurrence_count": count,
                        "ala_facet_fq": fq,
                    }
                )

        if len(page_items) < FACET_LIMIT:
            break

        facet_offset += FACET_LIMIT
        print(
            f"  facet page limit reached for {facet_field}; requesting offset={facet_offset:,}",
            flush=True,
        )

    return rows


def merge_taxa(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for row in rows:
        name = row["scientific_name"]

        if name not in merged:
            merged[name] = {
                "species_key": safe_key(name),
                "scientific_name": name,
                "orders": set(),
                "facet_fields": set(),
                "ala_occurrence_count": 0,
                "ala_facet_fq": set(),
            }

        merged[name]["orders"].add(row["order"])
        merged[name]["facet_fields"].add(row["facet_field"])
        merged[name]["ala_occurrence_count"] += row["ala_occurrence_count"]

        if row.get("ala_facet_fq"):
            merged[name]["ala_facet_fq"].add(row["ala_facet_fq"])

    out: list[dict[str, Any]] = []

    for item in merged.values():
        out.append(
            {
                "species_key": item["species_key"],
                "scientific_name": item["scientific_name"],
                "order": " | ".join(sorted(item["orders"])),
                "facet_fields": " | ".join(sorted(item["facet_fields"])),
                "ala_occurrence_count": item["ala_occurrence_count"],
                "ala_facet_fq": " | ".join(sorted(item["ala_facet_fq"])),
            }
        )

    return sorted(
        out,
        key=lambda x: (x["order"], x["scientific_name"], x["species_key"]),
    )


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "species_key",
                "scientific_name",
                "order",
                "facet_fields",
                "ala_occurrence_count",
                "ala_facet_fq",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def generate_species_targets(
    order: str = ORDER,
    *,
    output_dir: Path | str = OUTPUT_DIR,
) -> list[dict[str, Any]]:
    order = order.strip()
    output_dir = Path(output_dir)

    if not order:
        raise ValueError("Order must not be empty.")

    all_rows: list[dict[str, Any]] = []

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})

        for facet_field in FACET_FIELDS:
            print(
                f"Fetching {facet_field} facet for order={order}",
                flush=True,
            )
            order_rows = fetch_order_taxa(session, order, facet_field)
            print(
                f"  found {len(order_rows):,} {facet_field} names",
                flush=True,
            )
            all_rows.extend(order_rows)
            time.sleep(REQUEST_SLEEP_SECONDS)

    rows = merge_taxa(all_rows)

    if not rows:
        raise ValueError(f"No valid ALA species targets found for order {order!r}.")

    csv_path, json_path = output_paths(order, output_dir)

    write_csv(rows, csv_path)
    write_json(rows, json_path)

    print(f"\nUnique ALA-backed {order} scientific names: {len(rows):,}")
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")

    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate ALA-backed species targets for one Australian order."
    )
    parser.add_argument(
        "--order",
        default=ORDER,
        help=f"ALA order facet value to generate targets for. Default: {ORDER}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Directory for generated target files. Default: {OUTPUT_DIR}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generate_species_targets(order=args.order, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
