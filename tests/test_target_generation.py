from __future__ import annotations

import sys

import pytest

import alascraper as a


def test_coerce_species_target_accepts_generated_dict() -> None:
    target = a.coerce_species_target(
        {
            "scientific_name": "Papilio aegeus",
            "common_name": "Orchard Swallowtail",
            "taxon_lsid": "urn:lsid:test",
        }
    )

    assert target == a.SpeciesTarget(
        key="papilio_aegeus",
        scientific_name="Papilio aegeus",
        common_name="Orchard Swallowtail",
        taxon_lsid="urn:lsid:test",
    )


def test_coerce_species_target_rejects_missing_scientific_name() -> None:
    with pytest.raises(ValueError, match="scientific_name"):
        a.coerce_species_target({"key": "missing_name"})


def test_load_generated_species_targets_imports_generated_module(monkeypatch, tmp_path) -> None:
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    (outputs_dir / "__init__.py").write_text("", encoding="utf-8")
    generated_path = outputs_dir / "species_targets_generated.py"
    generated_path.write_text(
        "SPECIES_TARGETS = ["
        "{'key': 'papilio_aegeus', 'scientific_name': 'Papilio aegeus', "
        "'common_name': None, 'taxon_lsid': None}"
        "]\n",
        encoding="utf-8",
    )

    sys.modules.pop("outputs.species_targets_generated", None)
    sys.modules.pop("outputs", None)
    monkeypatch.setattr(a, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(a, "GENERATED_SPECIES_TARGETS_PATH", generated_path)

    targets = a.load_generated_species_targets()

    assert targets == [
        a.SpeciesTarget(
            key="papilio_aegeus",
            scientific_name="Papilio aegeus",
            common_name=None,
            taxon_lsid=None,
        )
    ]


def test_resolve_species_targets_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        a.resolve_species_targets(
            [
                {"key": "duplicate", "scientific_name": "Papilio aegeus"},
                {"key": "duplicate", "scientific_name": "Graphium sarpedon"},
            ],
            refresh_generated_targets=False,
        )
