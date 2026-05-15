from __future__ import annotations

import alascraper as a


def test_matching_metadata_retains_species_output(isolated_outputs, target: a.SpeciesTarget) -> None:
    a.prepare_species_output_for_config(target)
    sentinel = a.species_dir(target) / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    a.prepare_species_output_for_config(target)

    assert sentinel.exists()


def test_mismatched_metadata_deletes_species_output(monkeypatch, isolated_outputs, target: a.SpeciesTarget) -> None:
    a.prepare_species_output_for_config(target)
    sentinel = a.species_dir(target) / "sentinel.txt"
    sentinel.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(a, "PAGE_SIZE", a.PAGE_SIZE + 1)
    a.prepare_species_output_for_config(target)

    assert not sentinel.exists()
    assert a.species_metadata_path(target).exists()


def test_script_hash_only_change_retains_species_output(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
) -> None:
    a.prepare_species_output_for_config(target)
    sentinel = a.species_dir(target) / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(a, "script_sha256", lambda: "changed")
    a.prepare_species_output_for_config(target)

    assert sentinel.exists()


def test_resume_cache_version_change_deletes_species_output(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
) -> None:
    a.prepare_species_output_for_config(target)
    sentinel = a.species_dir(target) / "sentinel.txt"
    sentinel.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(a, "RESUME_CACHE_VERSION", a.RESUME_CACHE_VERSION + 1)
    a.prepare_species_output_for_config(target)

    assert not sentinel.exists()
