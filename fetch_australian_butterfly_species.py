#!/usr/bin/env python3.14
"""
Fetch scientific names of Australian butterfly taxa from ALA occurrence facets.

Output:
  outputs/australian_butterfly_species.csv
  outputs/australian_butterfly_species.json
  outputs/species_targets_generated.py

This creates an occurrence-backed ALA list, not a static taxonomic monograph.
Validate final taxonomy against Braby / Australian Faunal Directory for
publication workflows.
"""

from __future__ import annotations

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

OUTPUT_DIR = Path("outputs")
CSV_PATH = OUTPUT_DIR / "australian_butterfly_species.csv"
JSON_PATH = OUTPUT_DIR / "australian_butterfly_species.json"
PY_TARGETS_PATH = OUTPUT_DIR / "species_targets_generated.py"

# Lepidoptera LSID from ALA / Australian Faunal Directory.
LEPIDOPTERA_LSID = "https://biodiversity.org.au/afd/taxa/7cb6c81c-a7c4-4dd5-8578-fcfd2de847d6"

# Braby-style Australian butterfly families:
# Hesperioidea: Hesperiidae
# Papilionoidea: Papilionidae, Pieridae, Nymphalidae, Riodinidae, Lycaenidae
BUTTERFLY_FAMILIES = [
    "Hesperiidae",
    "Papilionidae",
    "Pieridae",
    "Nymphalidae",
    "Riodinidae",
    "Lycaenidae",
]

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
    "Monash-Australian-butterfly-species-list/0.2 "
    "(contact: replace-with-your-email@monash.edu)"
)


# =============================================================================
# HELPERS
# =============================================================================

def safe_key(scientific_name: str) -> str:
    text = scientific_name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def is_probable_scientific_name(name: str) -> bool:
    """
    Keep species/subspecies-like names; exclude genus/family/order labels.
    Allows names like `Papilio aegeus` and `Papilio aegeus aegeus`.
    """
    parts = name.strip().split()

    if len(parts) < 2:
        return False

    return bool(re.match(r"^[A-Z][a-zA-Z-]+$", parts[0]))


def build_params(family: str, facet_field: str) -> list[tuple[str, str | int]]:
    return [
        ("q", f"lsid:{LEPIDOPTERA_LSID}"),
        ("fq", COUNTRY_FILTER),
        ("fq", f'family:"{family}"'),
        ("qualityProfile", QUALITY_PROFILE),
        ("qc", QUALITY_CONTROL),
        ("pageSize", 0),
        ("facet", "true"),
        ("facets", facet_field),
        ("flimit", FACET_LIMIT),
    ]


def fetch_family_taxa(
    session: requests.Session,
    family: str,
    facet_field: str,
) -> list[dict[str, Any]]:
    response = session.get(
        API_URL,
        params=build_params(family, facet_field),
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
                    "family": family,
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
                "families": set(),
                "facet_fields": set(),
                "ala_occurrence_count": 0,
                "ala_facet_fq": set(),
            }

        merged[name]["families"].add(row["family"])
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
                "families": " | ".join(sorted(item["families"])),
                "facet_fields": " | ".join(sorted(item["facet_fields"])),
                "ala_occurrence_count": item["ala_occurrence_count"],
                "ala_facet_fq": " | ".join(sorted(item["ala_facet_fq"])),
            }
        )

    return sorted(out, key=lambda x: (x["families"], x["scientific_name"]))


def write_csv(rows: list[dict[str, Any]]) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "species_key",
                "scientific_name",
                "families",
                "facet_fields",
                "ala_occurrence_count",
                "ala_facet_fq",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict[str, Any]]) -> None:
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_python_targets(rows: list[dict[str, Any]]) -> None:
    """
    Writes plain dictionaries so alascraper.py can import and coerce them
    without circular-importing its SpeciesTarget class.
    """
    lines: list[str] = [
        "# Generated by fetch_australian_butterfly_species.py",
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
            "families": row["families"],
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


def generate_species_targets() -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})

        for family in BUTTERFLY_FAMILIES:
            for facet_field in FACET_FIELDS:
                print(
                    f"Fetching {facet_field} facet for family={family}",
                    flush=True,
                )
                family_rows = fetch_family_taxa(session, family, facet_field)
                print(
                    f"  found {len(family_rows):,} {facet_field} names",
                    flush=True,
                )
                all_rows.extend(family_rows)
                time.sleep(REQUEST_SLEEP_SECONDS)

    rows = merge_taxa(all_rows)

    write_csv(rows)
    write_json(rows)
    write_python_targets(rows)

    print(f"\nUnique ALA-backed butterfly scientific names: {len(rows):,}")
    print(f"Wrote: {CSV_PATH}")
    print(f"Wrote: {JSON_PATH}")
    print(f"Wrote: {PY_TARGETS_PATH}")

    return rows


def main() -> int:
    generate_species_targets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
