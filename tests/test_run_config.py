from __future__ import annotations

import alascraper as a


def test_species_run_config_contains_query_filters_schema_and_species(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "script_sha256", lambda: "fixed-script-hash")

    config = a.species_run_config(target)

    assert config["script_sha256"] == "fixed-script-hash"
    assert config["query"] == a.build_query(target)
    assert config["fq_filters"] == a.build_fq_filters(target)
    assert config["fields"] == a.FIELDS
    assert config["schema"] == a.schema_signature()
    assert config["species_target"]["key"] == target.key
    assert config["species_target"]["scientific_name"] == target.scientific_name


def test_config_fingerprint_is_deterministic(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "script_sha256", lambda: "fixed-script-hash")

    config = a.species_run_config(target)

    assert a.config_fingerprint(config) == a.config_fingerprint(config)


def test_config_fingerprint_changes_when_species_changes(monkeypatch, target: a.SpeciesTarget, lsid_target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "script_sha256", lambda: "fixed-script-hash")

    assert a.config_fingerprint(a.species_run_config(target)) != a.config_fingerprint(
        a.species_run_config(lsid_target)
    )


def test_config_fingerprint_changes_when_page_size_changes(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "script_sha256", lambda: "fixed-script-hash")
    before = a.config_fingerprint(a.species_run_config(target))

    monkeypatch.setattr(a, "PAGE_SIZE", a.PAGE_SIZE + 1)
    after = a.config_fingerprint(a.species_run_config(target))

    assert before != after


def test_config_fingerprint_changes_when_privacy_setting_changes(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "script_sha256", lambda: "fixed-script-hash")
    before = a.config_fingerprint(a.species_run_config(target))

    monkeypatch.setattr(a, "INCLUDE_USER_DATA_FIELDS", not a.INCLUDE_USER_DATA_FIELDS)
    after = a.config_fingerprint(a.species_run_config(target))

    assert before != after


def test_config_fingerprint_changes_when_filters_change(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "script_sha256", lambda: "fixed-script-hash")
    before = a.config_fingerprint(a.species_run_config(target))

    monkeypatch.setattr(a, "COUNTRY_FILTER_ENABLED", False)
    after = a.config_fingerprint(a.species_run_config(target))

    assert before != after


def test_config_fingerprint_changes_when_script_hash_changes(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "script_sha256", lambda: "script-hash-1")
    before = a.config_fingerprint(a.species_run_config(target))

    monkeypatch.setattr(a, "script_sha256", lambda: "script-hash-2")
    after = a.config_fingerprint(a.species_run_config(target))

    assert before != after
