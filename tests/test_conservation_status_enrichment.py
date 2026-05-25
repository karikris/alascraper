from __future__ import annotations

from pathlib import Path

import polars as pl

from scripts.cleaning import enrich_butterfly_conservation_status as enrich


BASE_COLUMNS = {
    "uuid": pl.String,
    "family": pl.String,
    "genus": pl.String,
    "species": pl.String,
    "scientificName": pl.String,
    "stateProvince": pl.String,
    "year": pl.Int64,
    "decimalLatitude": pl.Float64,
    "decimalLongitude": pl.Float64,
}


def write_occurrence_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [
            {
                "uuid": "1",
                "family": "Hesperiidae",
                "genus": "Antipodia",
                "species": "Antipodia chaostola",
                "scientificName": "Antipodia chaostola leucophaea",
                "stateProvince": "Tasmania",
                "year": 2020,
                "decimalLatitude": -42.0,
                "decimalLongitude": 147.0,
            },
            {
                "uuid": "2",
                "family": "Lycaenidae",
                "genus": "Paralucia",
                "species": "Paralucia spinifera",
                "scientificName": "Paralucia spinifera",
                "stateProvince": "New South Wales",
                "year": 2021,
                "decimalLatitude": -33.5,
                "decimalLongitude": 150.1,
            },
            {
                "uuid": "3",
                "family": "Lycaenidae",
                "genus": "Jalmenus",
                "species": "Jalmenus eubulus",
                "scientificName": "Jalmenus eubulus variant",
                "stateProvince": "Queensland",
                "year": 2022,
                "decimalLatitude": -27.0,
                "decimalLongitude": 153.0,
            },
            {
                "uuid": "4",
                "family": "Hesperiidae",
                "genus": "Oreisplanus",
                "species": "Oreisplanus munionga",
                "scientificName": "Oreisplanus munionga larana",
                "stateProvince": "Tasmania",
                "year": 2023,
                "decimalLatitude": -41.0,
                "decimalLongitude": 145.0,
            },
            {
                "uuid": "5",
                "family": "Nymphalidae",
                "genus": "Junonia",
                "species": "Junonia villida",
                "scientificName": "Junonia villida",
                "stateProvince": "Victoria",
                "year": 2024,
                "decimalLatitude": -37.8,
                "decimalLongitude": 144.9,
            },
        ],
        schema=BASE_COLUMNS,
        orient="row",
    ).write_parquet(path)


def write_reference_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [
            {
                "accepted_taxon": "Antipodia chaostola leucophaea",
                "match_names": "Antipodia chaostola leucophaea",
                "rank": "subspecies",
                "common_name": "Tasmanian Chaostola Skipper",
                "epbc_status": "Endangered",
                "epbc_listed_id": "77672",
                "epbc_sprat_url": "https://www.environment.gov.au/cgi-bin/sprat/public/publicspecies.pl?taxon_id=77672",
                "epbc_conservation_advice_url": "",
                "epbc_recovery_plan_url": "",
                "epbc_protected_matters_url": "",
                "state_status": "Endangered",
                "state_status_jurisdiction": "TAS",
                "state_source_url": "https://nre.tas.gov.au/example-chaostola.pdf",
                "source_dataset": "test reference",
                "source_date": "2026-05-25",
                "notes": "Exact subspecies match.",
            },
            {
                "accepted_taxon": "Paralucia spinifera",
                "match_names": "Paralucia spinifera",
                "rank": "species",
                "common_name": "Purple Copper",
                "epbc_status": "Vulnerable",
                "epbc_listed_id": "26330",
                "epbc_sprat_url": "https://www.environment.gov.au/cgi-bin/sprat/public/publicspecies.pl?taxon_id=26330",
                "epbc_conservation_advice_url": "",
                "epbc_recovery_plan_url": "",
                "epbc_protected_matters_url": "",
                "state_status": "Endangered",
                "state_status_jurisdiction": "NSW",
                "state_source_url": "https://www.environment.nsw.gov.au/example-purple-copper.pdf",
                "source_dataset": "test reference",
                "source_date": "2026-05-25",
                "notes": "Species-level match.",
            },
            {
                "accepted_taxon": "Jalmenus eubulus",
                "match_names": "Jalmenus eubulus",
                "rank": "species",
                "common_name": "Pale Imperial Hairstreak",
                "epbc_status": "",
                "epbc_listed_id": "",
                "epbc_sprat_url": "",
                "epbc_conservation_advice_url": "",
                "epbc_recovery_plan_url": "",
                "epbc_protected_matters_url": "",
                "state_status": "NSW: Critically Endangered; QLD: Vulnerable",
                "state_status_jurisdiction": "NSW; QLD",
                "state_source_url": "https://threatenedspecies.bionet.nsw.gov.au/profile?id=20123",
                "source_dataset": "test reference",
                "source_date": "2026-05-25",
                "notes": "State listed only.",
            },
            {
                "accepted_taxon": "Hesperilla munionga larana",
                "match_names": "Hesperilla munionga larana|Oreisplanus munionga larana",
                "rank": "subspecies",
                "common_name": "Marrawah Skipper",
                "epbc_status": "Vulnerable",
                "epbc_listed_id": "94585",
                "epbc_sprat_url": "https://www.environment.gov.au/cgi-bin/sprat/public/publicspecies.pl?taxon_id=94585",
                "epbc_conservation_advice_url": "",
                "epbc_recovery_plan_url": "",
                "epbc_protected_matters_url": "",
                "state_status": "Endangered",
                "state_status_jurisdiction": "TAS",
                "state_source_url": "https://nre.tas.gov.au/example-marrawah.pdf",
                "source_dataset": "test reference",
                "source_date": "2026-05-25",
                "notes": "Synonym match.",
            },
        ]
    ).write_csv(path)


def test_enrichment_adds_epbc_state_and_match_provenance_columns(tmp_path: Path) -> None:
    source = tmp_path / "butterflies_cleaned.parquet"
    reference = tmp_path / "butterfly_conservation_status.csv"
    output = tmp_path / "butterflies_conservation.parquet"
    report = tmp_path / "conservation_status_enrichment_report.json"
    write_occurrence_fixture(source)
    write_reference_fixture(reference)

    result = enrich.enrich_conservation_status(
        source_path=source,
        reference_path=reference,
        output_path=output,
        report_path=report,
    )

    df = pl.read_parquet(result.output_parquet).sort("uuid")
    rows = {row["uuid"]: row for row in df.iter_rows(named=True)}

    assert result.row_count == 5
    assert result.epbc_matched_rows == 3
    assert result.state_matched_rows == 4
    assert rows["1"]["Status"] == "Endangered"
    assert rows["1"]["epbc_match_type"] == "scientificName"
    assert rows["2"]["Status"] == "Vulnerable"
    assert rows["2"]["state_status"] == "Endangered"
    assert rows["3"]["Status"] is None
    assert rows["3"]["state_status"] == "NSW: Critically Endangered; QLD: Vulnerable"
    assert rows["3"]["state_status_level"] == "Vulnerable"
    assert rows["3"]["state_status_for_occurrence"] == "QLD: Vulnerable"
    assert rows["3"]["state_status_jurisdiction_matched"] == "QLD"
    assert rows["3"]["state_match_type"] == "species"
    assert rows["4"]["Status"] == "Vulnerable"
    assert rows["4"]["epbc_listed_taxon"] == "Hesperilla munionga larana"
    assert rows["4"]["epbc_match_type"] == "scientificName_synonym"
    assert rows["5"]["Status"] is None
    assert rows["5"]["state_status"] is None
    assert report.exists()


def test_enrichment_output_schema_contains_dashboard_status_columns(tmp_path: Path) -> None:
    source = tmp_path / "butterflies_cleaned.parquet"
    reference = tmp_path / "butterfly_conservation_status.csv"
    output = tmp_path / "butterflies_conservation.parquet"
    write_occurrence_fixture(source)
    write_reference_fixture(reference)

    enrich.enrich_conservation_status(
        source_path=source,
        reference_path=reference,
        output_path=output,
        report_path=None,
    )

    schema = pl.read_parquet(output).schema

    for column in enrich.CONSERVATION_OUTPUT_COLUMNS:
        assert column in schema
