from __future__ import annotations

import sys

import pytest

import alascraper as a
import fetch_by_order as generator


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


def test_fetch_by_order_cli_defaults_to_order_constant() -> None:
    args = generator.parse_args([])

    assert args.order == generator.ORDER


def test_fetch_by_order_cli_accepts_order_override() -> None:
    args = generator.parse_args(["--order", "Neuroptera"])

    assert args.order == "Neuroptera"


def test_build_params_queries_australian_order_facets() -> None:
    params = generator.build_params("Neuroptera", "species")

    assert ("q", "*:*") in params
    assert ("fq", 'country:"Australia"') in params
    assert ("fq", 'order:"Neuroptera"') in params
    assert ("facets", "species") in params


def test_merge_taxa_deduplicates_species_and_subspecies_rows() -> None:
    rows = [
        {
            "species_key": "acacia_example",
            "scientific_name": "Acacia example",
            "order": "Fabales",
            "facet_field": "species",
            "ala_occurrence_count": 7,
            "ala_facet_fq": 'species:"Acacia example"',
        },
        {
            "species_key": "acacia_example",
            "scientific_name": "Acacia example",
            "order": "Fabales",
            "facet_field": "subspecies",
            "ala_occurrence_count": 3,
            "ala_facet_fq": 'subspecies:"Acacia example"',
        },
    ]

    merged = generator.merge_taxa(rows)

    assert merged == [
        {
            "species_key": "acacia_example",
            "scientific_name": "Acacia example",
            "order": "Fabales",
            "facet_fields": "species | subspecies",
            "ala_occurrence_count": 10,
            "ala_facet_fq": 'species:"Acacia example" | subspecies:"Acacia example"',
        }
    ]


def test_write_python_targets_contains_species_targets(monkeypatch, tmp_path) -> None:
    generated_path = tmp_path / "outputs" / "species_targets_generated.py"
    monkeypatch.setattr(generator, "PY_TARGETS_PATH", generated_path)

    generator.write_python_targets(
        [
            {
                "species_key": "papilio_aegeus",
                "scientific_name": "Papilio aegeus",
                "order": "Lepidoptera",
                "facet_fields": "species",
                "ala_occurrence_count": 42,
                "ala_facet_fq": 'species:"Papilio aegeus"',
            }
        ]
    )

    text = generated_path.read_text(encoding="utf-8")

    assert "SPECIES_TARGETS = [" in text
    assert "'key': 'papilio_aegeus'" in text
    assert "'order': 'Lepidoptera'" in text


def test_generate_species_targets_rejects_empty_valid_target_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(generator, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(
        generator,
        "PY_TARGETS_PATH",
        tmp_path / "outputs" / "species_targets_generated.py",
    )
    monkeypatch.setattr(generator, "REQUEST_SLEEP_SECONDS", 0)
    monkeypatch.setattr(generator, "fetch_order_taxa", lambda session, order, facet_field: [])

    with pytest.raises(ValueError, match="No valid ALA species targets found"):
        generator.generate_species_targets(order="Neuroptera")

    assert not (tmp_path / "outputs" / "species_targets_generated.py").exists()
