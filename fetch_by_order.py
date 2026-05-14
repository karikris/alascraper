#!/usr/bin/env python3.14
"""
Fetch scientific names of Australian taxa for one order from ALA occurrence facets.

Output:
  outputs/<order_key>_species.csv
  outputs/<order_key>_species.json
  outputs/species_targets_generated.py

This creates an occurrence-backed ALA list for Australia-only records, not a
static taxonomic monograph. Validate final taxonomy against GBIF and relevant
taxon-specific authorities for publication workflows.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from typing import Any

import requests


# =============================================================================
# USER CONSTANTS
# =============================================================================

API_URL = "https://biocache-ws.ala.org.au/ws/occurrences/search"

ORDER = "Lepidoptera"

OUTPUT_DIR = Path("outputs")
PY_TARGETS_PATH = OUTPUT_DIR / "species_targets_generated.py"

COUNTRY_FILTER = 'country:"Australia"'
QUALITY_PROFILE = "ALA"
QUALITY_CONTROL = "-_nest_parent_:*"

# `species` is the stable baseline. `subspecies` is included where ALA facets
# expose lower taxa, supporting the 596 species/subspecies target.
FACET_FIELDS = ["species", "subspecies"]
FACET_LIMIT = 1000
REQUEST_SLEEP_SECONDS = 0.3
TIMEOUT_SECONDS = 60

USER_AGENT = (
    "Monash-ALA-order-species-list/0.3 "
    "(contact: replace-with-your-email@monash.edu)"
)

INVALID_TAXON_LABELS = {
    "not supplied",
    "not provided",
    "not recorded",
    "unknown",
    "unidentified",
    "other values",
}


# =============================================================================
# HELPERS
# =============================================================================

def safe_key(scientific_name: str) -> str:
    text = scientific_name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def output_paths(order: str) -> tuple[Path, Path]:
    order_key = safe_key(order)
    return (
        OUTPUT_DIR / f"{order_key}_species.csv",
        OUTPUT_DIR / f"{order_key}_species.json",
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


def build_params(order: str, facet_field: str) -> list[tuple[str, str | int]]:
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
    ]


def fetch_order_taxa(
    session: requests.Session,
    order: str,
    facet_field: str,
) -> list[dict[str, Any]]:
    response = session.get(
        API_URL,
        params=build_params(order, facet_field),
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()

    rows: list[dict[str, Any]] = []

    for facet in data.get("facetResults", []):
        if facet.get("fieldName") != facet_field:
            continue

        for item in facet.get("fieldResult", []):
            scientific_name = item.get("label")
            count = int(item.get("count", 0) or 0)
            fq = item.get("fq")

            if not scientific_name or count <= 0:
                continue

            if not is_probable_scientific_name(scientific_name):
                continue

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


def write_python_targets(rows: list[dict[str, Any]]) -> None:
    """
    Writes plain dictionaries so alascraper.py can import and coerce them
    without circular-importing its SpeciesTarget class.
    """
    lines: list[str] = [
        "# Generated by fetch_by_order.py",
        "# Do not edit by hand; rerun the generator to refresh.",
        "",
        "SPECIES_TARGETS = [",
    ]

    for row in rows:
        target = {
            "key": row["species_key"],
            "scientific_name": row["scientific_name"],
            "common_name": None,
            "taxon_lsid": None,
            "order": row["order"],
            "facet_fields": row["facet_fields"],
            "ala_occurrence_count": row["ala_occurrence_count"],
            "ala_facet_fq": row["ala_facet_fq"],
        }
        lines.append(f"    {target!r},")

    lines.append("]")
    lines.append("")

    PY_TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    (PY_TARGETS_PATH.parent / "__init__.py").write_text("", encoding="utf-8")
    PY_TARGETS_PATH.write_text("\n".join(lines), encoding="utf-8")


def generate_species_targets(order: str = ORDER) -> list[dict[str, Any]]:
    order = order.strip()

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

    csv_path, json_path = output_paths(order)

    write_csv(rows, csv_path)
    write_json(rows, json_path)
    write_python_targets(rows)

    print(f"\nUnique ALA-backed {order} scientific names: {len(rows):,}")
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")
    print(f"Wrote: {PY_TARGETS_PATH}")

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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generate_species_targets(order=args.order)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
