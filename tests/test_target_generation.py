from __future__ import annotations

import pytest

import alascraper as a
import scripts.fetch_by_order as generator


def test_coerce_species_target_accepts_generated_dict() -> None:
    target = a.coerce_species_target(
        {
            "scientific_name": "Papilio aegeus",
            "common_name": "Orchard Swallowtail",
            "taxon_lsid": "urn:lsid:test",
            "order": "Lepidoptera",
            "ala_facet_fq": 'species:"Papilio aegeus" | subspecies:"Papilio aegeus"',
        }
    )

    assert target == a.SpeciesTarget(
        key="papilio_aegeus",
        scientific_name="Papilio aegeus",
        common_name="Orchard Swallowtail",
        taxon_lsid="urn:lsid:test",
        source_order="Lepidoptera",
        facet_fq_filters=(
            'species:"Papilio aegeus"',
            'subspecies:"Papilio aegeus"',
        ),
    )


def test_coerce_species_target_rejects_missing_scientific_name() -> None:
    with pytest.raises(ValueError, match="scientific_name"):
        a.coerce_species_target({"key": "missing_name"})


def test_load_generated_species_targets_imports_generated_module(monkeypatch, tmp_path) -> None:
    dataset_dir = tmp_path / "datasets" / "insecta" / "lepidoptera"
    dataset_dir.mkdir(parents=True)
    generated_path = dataset_dir / "species_targets_generated.py"
    generated_path.write_text(
        "SPECIES_TARGETS = ["
        "{'key': 'papilio_aegeus', 'scientific_name': 'Papilio aegeus', "
        "'common_name': None, 'taxon_lsid': None, 'order': 'Lepidoptera', "
        "'ala_facet_fq': 'species:\"Papilio aegeus\"'}"
        "]\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(a, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(a, "GENERATED_SPECIES_TARGETS_PATH", generated_path)

    targets = a.load_generated_species_targets()

    assert targets == [
        a.SpeciesTarget(
            key="papilio_aegeus",
            scientific_name="Papilio aegeus",
            common_name=None,
            taxon_lsid=None,
            source_order="Lepidoptera",
            facet_fq_filters=('species:"Papilio aegeus"',),
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


def test_alascraper_cli_accepts_order_and_csv_toggle() -> None:
    args = a.parse_args(["--class", "insecta", "--order", "Lepidoptera", "TRUE"])

    assert args.dataset_class == "insecta"
    assert args.order == "Lepidoptera"
    assert args.write_csv is True


def test_alascraper_cli_defaults_csv_toggle_to_none() -> None:
    args = a.parse_args(["--order", "Neuroptera"])

    assert args.order == "Neuroptera"
    assert args.write_csv is None


def test_dataset_output_root_uses_class_and_order() -> None:
    assert a.dataset_output_root("Poales", "monocot") == (
        a.DATASETS_ROOT / "monocot" / "poales"
    )


def test_dataset_output_root_defaults_missing_class_to_misc() -> None:
    assert a.dataset_output_root("Poales", None) == (
        a.DATASETS_ROOT / "misc" / "poales"
    )


def test_alascraper_main_passes_order_and_csv_toggle(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_alascraper(
        *,
        order: str | None,
        dataset_class: str | None,
        write_csv: bool | None,
        **kwargs: object,
    ) -> int:
        captured["order"] = order
        captured["dataset_class"] = dataset_class
        captured["write_csv"] = write_csv
        return 0

    monkeypatch.setattr(a, "run_alascraper", fake_run_alascraper)

    assert a.main(["--class", "insecta", "--order", "Lepidoptera", "TRUE"]) == 0
    assert captured == {
        "order": "Lepidoptera",
        "dataset_class": "insecta",
        "write_csv": True,
    }


def test_generate_species_targets_file_passes_order(monkeypatch, tmp_path) -> None:
    captured: dict[str, str | None] = {}

    class FakeGenerator:
        @staticmethod
        def generate_species_targets(
            order: str | None = None,
            *,
            output_dir: object | None = None,
        ) -> None:
            captured["order"] = order
            captured["output_dir"] = str(output_dir)

    monkeypatch.setattr(
        a,
        "GENERATED_SPECIES_TARGETS_PATH",
        tmp_path / "species_targets_generated.py",
    )
    monkeypatch.setattr(
        a,
        "SPECIES_TARGETS_GENERATOR_SCRIPT",
        tmp_path / "fetch_by_order.py",
    )
    a.SPECIES_TARGETS_GENERATOR_SCRIPT.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        a.importlib,
        "import_module",
        lambda module_name: FakeGenerator,
    )

    a.generate_species_targets_file(refresh=True, order="Mantodea")

    assert captured == {
        "order": "Mantodea",
        "output_dir": str(a.OUTPUT_ROOT),
    }


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
    assert ("foffset", 0) in params


def test_fetch_order_taxa_pages_past_facet_limit(monkeypatch) -> None:
    monkeypatch.setattr(generator, "FACET_LIMIT", 1)

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    class FakeSession:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def get(self, _url: str, *, params, timeout: int):  # type: ignore[no-untyped-def]
            params_dict = dict(params)
            offset = int(params_dict["foffset"])
            self.offsets.append(offset)
            rows = (
                [{"label": "Poa annua", "count": 2, "fq": 'species:"Poa annua"'}]
                if offset == 0
                else []
            )
            return FakeResponse(
                {
                    "facetResults": [
                        {
                            "fieldName": "species",
                            "fieldResult": rows,
                        }
                    ]
                }
            )

    session = FakeSession()
    rows = generator.fetch_order_taxa(session, "Poales", "species")

    assert session.offsets == [0, 1]
    assert [row["scientific_name"] for row in rows] == ["Poa annua"]


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


def test_write_python_targets_contains_species_targets(tmp_path) -> None:
    generated_path = (
        tmp_path / "datasets" / "insecta" / "lepidoptera" / "species_targets_generated.py"
    )

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
        ],
        generated_path,
    )

    text = generated_path.read_text(encoding="utf-8")

    assert "SPECIES_TARGETS = [" in text
    assert "'key': 'papilio_aegeus'" in text
    assert "'order': 'Lepidoptera'" in text


def test_generate_species_targets_rejects_empty_valid_target_list(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "datasets" / "monocot" / "poales"
    monkeypatch.setattr(generator, "REQUEST_SLEEP_SECONDS", 0)
    monkeypatch.setattr(generator, "fetch_order_taxa", lambda session, order, facet_field: [])

    with pytest.raises(ValueError, match="No valid ALA species targets found"):
        generator.generate_species_targets(order="Neuroptera", output_dir=output_dir)

    assert not (output_dir / "species_targets_generated.py").exists()
