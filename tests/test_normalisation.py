from __future__ import annotations

import json

import alascraper as a


def test_normalise_text_cell_handles_scalars_lists_and_dicts() -> None:
    assert a.normalise_text_cell(None) is None
    assert a.normalise_text_cell("text") == "text"
    assert a.normalise_text_cell(["a", None, "b", 3]) == "a | b | 3"
    assert a.normalise_text_cell({"b": 2, "a": 1}) == json.dumps(
        {"b": 2, "a": 1}, ensure_ascii=False, separators=(",", ":")
    )


def test_numeric_coercion() -> None:
    assert a.to_float("1.25") == 1.25
    assert a.to_float(3) == 3.0
    assert a.to_float("") is None
    assert a.to_float("not-a-number") is None

    assert a.to_int("7") == 7
    assert a.to_int(9) == 9
    assert a.to_int("") is None
    assert a.to_int("7.5") is None


def test_boolean_coercion() -> None:
    for value in (True, "true", "T", "1", "yes", "Y"):
        assert a.to_bool(value) is True

    for value in (False, "false", "F", "0", "no", "N"):
        assert a.to_bool(value) is False

    assert a.to_bool(None) is None
    assert a.to_bool("unknown") is None


def test_epoch_millis_to_iso() -> None:
    assert a.epoch_millis_to_iso(0) == "1970-01-01T00:00:00+00:00"
    assert a.epoch_millis_to_iso(1_000) == "1970-01-01T00:00:01+00:00"
    assert a.epoch_millis_to_iso(None) is None


def test_normalise_taxon_name_removes_subgenus_and_punctuation() -> None:
    assert a.normalise_taxon_name("Papilio (Princeps) aegeus") == "papilio aegeus"
    assert a.normalise_taxon_name("  Danaus--plexippus  ") == "danaus plexippus"
    assert a.normalise_taxon_name(None) is None


def test_normalise_record_handles_missing_and_extra_ala_fields(target: a.SpeciesTarget) -> None:
    record = {
        "uuid": "record-1",
        "scientificName": "Testus species",
        "decimalLatitude": "-37.81",
        "decimalLongitude": "144.96",
        "eventDate": "1000",
        "spatiallyValid": "true",
        "unusedExtraField": "ignored",
    }

    row = a.normalise_record(target, record)

    assert set(row) == set(a.FIELDS)
    assert row["query_species_key"] == target.key
    assert row["query_scientific_name"] == target.scientific_name
    assert row["uuid"] == "record-1"
    assert row["decimalLatitude"] == -37.81
    assert row["decimalLongitude"] == 144.96
    assert row["eventDate"] == 1000
    assert row["eventDate_iso"] == "1970-01-01T00:00:01+00:00"
    assert row["spatiallyValid"] is True
    assert row["vernacularName"] is None
    assert "unusedExtraField" not in row
