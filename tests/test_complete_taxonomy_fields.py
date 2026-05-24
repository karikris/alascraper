from __future__ import annotations

import csv
import json
from pathlib import Path

import polars as pl

from scripts.cleaning import complete_taxonomy_fields as cleaner


def write_taxonomy_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "uuid": ["1", "2", "3", "4", "5"],
            "scientificName": [
                "Exactus alpha",
                "Exactus alpha",
                "Lookup beta",
                "GenusOnly",
                "FAMILYONLY",
            ],
            "taxonRank": ["species", "species", "species", "genus", "family"],
            "genus": ["Exactus", None, None, "GenusOnly", None],
            "species": ["Exactus alpha", None, None, None, None],
            "month": ["01", "13", None, "01", "02"],
        }
    ).write_parquet(path)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_complete_taxonomy_fields_fills_exact_and_external_matches(
    tmp_path: Path,
) -> None:
    source = tmp_path / "butterflies.parquet"
    output = tmp_path / "butterflies_cleaned.parquet"
    report_dir = tmp_path / "quality_reports"
    write_taxonomy_fixture(source)

    def fake_lookup(scientific_name: str) -> cleaner.TaxonomyLookupResult | None:
        if scientific_name == "Lookup beta":
            return cleaner.TaxonomyLookupResult(
                source="gbif",
                rank="species",
                genus="Lookup",
                species="Lookup beta",
                accepted=True,
            )
        return None

    result = cleaner.complete_taxonomy_fields(
        source_path=source,
        output_path=output,
        report_dir=report_dir,
        external_lookup=fake_lookup,
    )

    cleaned = pl.read_parquet(output).sort("uuid")
    metadata = json.loads(result.report_json.read_text(encoding="utf-8"))
    missing_rows = read_csv_rows(result.missing_rows_csv)
    month_rows = read_csv_rows(result.month_counts_csv)

    assert cleaned.height == 5
    assert cleaned.filter(pl.col("uuid") == "2")["genus"].item() == "Exactus"
    assert cleaned.filter(pl.col("uuid") == "2")["species"].item() == "Exactus alpha"
    assert cleaned.filter(pl.col("uuid") == "3")["genus"].item() == "Lookup"
    assert cleaned.filter(pl.col("uuid") == "3")["species"].item() == "Lookup beta"
    assert cleaned.filter(pl.col("uuid") == "4")["species"].item() is None
    assert cleaned.filter(pl.col("uuid") == "5")["genus"].item() is None
    assert metadata["input_rows"] == 5
    assert metadata["output_rows"] == 5
    assert metadata["exact_fill_rows"] == 1
    assert metadata["external_fill_rows"] == 1
    assert metadata["remaining_missing_rows"] == 2
    assert {row["scientificName"] for row in missing_rows} == {"GenusOnly", "FAMILYONLY"}
    assert month_rows == [
        {"month": "01", "rows": "2"},
        {"month": "02", "rows": "1"},
        {"month": "13", "rows": "1"},
        {"month": "", "rows": "1"},
    ]


def test_external_lookup_rejects_non_species_rank(tmp_path: Path) -> None:
    source = tmp_path / "butterflies.parquet"
    output = tmp_path / "butterflies_cleaned.parquet"
    report_dir = tmp_path / "quality_reports"
    write_taxonomy_fixture(source)

    def fake_lookup(_scientific_name: str) -> cleaner.TaxonomyLookupResult:
        return cleaner.TaxonomyLookupResult(
            source="ala",
            rank="genus",
            genus="Unsafe",
            species="Unsafe inferred",
            accepted=True,
        )

    result = cleaner.complete_taxonomy_fields(
        source_path=source,
        output_path=output,
        report_dir=report_dir,
        external_lookup=fake_lookup,
    )

    cleaned = pl.read_parquet(output)
    metadata = json.loads(result.report_json.read_text(encoding="utf-8"))

    assert cleaned.filter(pl.col("uuid") == "3")["species"].item() is None
    assert metadata["external_fill_rows"] == 0
    assert metadata["rejected_external_lookup_count"] == 3


def test_species_binomial_from_name_removes_subgenus() -> None:
    assert cleaner.species_binomial_from_name("Papilio (Princeps) aegeus") == (
        "Papilio",
        "Papilio aegeus",
    )
