from __future__ import annotations

import polars as pl

import alascraper as a


def test_merge_species_shards_dedupes_duplicate_uuid(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
    record_row,
    parquet_shard_writer,
) -> None:
    monkeypatch.setattr(a, "DEDUPE_BY_UUID", True)
    rows = [
        record_row(target, uuid="dup", event_date=2_000),
        record_row(target, uuid="dup", event_date=1_000),
        record_row(target, uuid="unique", event_date=3_000),
    ]
    parquet_shard_writer(target, 0, rows)

    output = a.merge_species_shards(target)
    df = pl.read_parquet(output)

    assert df.height == 2
    assert set(df["uuid"].to_list()) == {"dup", "unique"}
    assert df.filter(pl.col("uuid") == "dup")["eventDate"].item() == 1_000


def test_merge_species_shards_dedupes_null_uuid_fingerprint(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
    record_row,
    parquet_shard_writer,
) -> None:
    monkeypatch.setattr(a, "DEDUPE_BY_UUID", True)
    duplicate_a = record_row(target, uuid=None, event_date=1_000)
    duplicate_b = record_row(target, uuid=None, event_date=2_000)
    unique = record_row(target, uuid=None, event_date=3_000, scientific_name="Other species")

    for row in (duplicate_a, duplicate_b, unique):
        row["decimalLatitude"] = -37.1
        row["decimalLongitude"] = 145.2
        row["dataResourceUid"] = "dr1"
        row["recordNumber"] = "rn1"
        row["basisOfRecord"] = "HUMAN_OBSERVATION"

    duplicate_b["eventDate"] = duplicate_a["eventDate"]

    parquet_shard_writer(target, 0, [duplicate_a, duplicate_b, unique])

    output = a.merge_species_shards(target)
    df = pl.read_parquet(output)

    assert df.height == 2
    assert set(df["scientificName"].to_list()) == {"Testus species", "Other species"}


def test_merge_all_species_dedupes_across_species(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
    lsid_target: a.SpeciesTarget,
    record_row,
) -> None:
    monkeypatch.setattr(a, "DEDUPE_BY_UUID", True)
    monkeypatch.setattr(a, "WRITE_DUCKDB_DATABASE", False)
    monkeypatch.setattr(a, "WRITE_CSV", False)

    first_path = a.species_parquet_path(target)
    second_path = a.species_parquet_path(lsid_target)
    first_path.parent.mkdir(parents=True, exist_ok=True)
    second_path.parent.mkdir(parents=True, exist_ok=True)

    pl.DataFrame(
        [
            record_row(target, uuid="shared", event_date=2_000),
            record_row(target, uuid="first-only", event_date=3_000),
        ],
        schema=a.SCHEMA,
        orient="row",
    ).write_parquet(first_path)
    pl.DataFrame(
        [
            record_row(lsid_target, uuid="shared", event_date=1_000),
            record_row(lsid_target, uuid="second-only", event_date=4_000),
        ],
        schema=a.SCHEMA,
        orient="row",
    ).write_parquet(second_path)

    results = [
        a.SpeciesResult(target.key, target.scientific_name, target.common_name, target.taxon_lsid, "fp1", 2, 1, 2, 0.1, first_path),
        a.SpeciesResult(lsid_target.key, lsid_target.scientific_name, lsid_target.common_name, lsid_target.taxon_lsid, "fp2", 2, 1, 2, 0.1, second_path),
    ]

    a.merge_all_species(results)
    df = pl.read_parquet(a.FINAL_ALL_SPECIES_PARQUET)

    assert df.height == 3
    assert set(df["uuid"].to_list()) == {"shared", "first-only", "second-only"}
    assert df.filter(pl.col("uuid") == "shared")["eventDate"].item() == 1_000
