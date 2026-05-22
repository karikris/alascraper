from __future__ import annotations

import json
from pathlib import Path

import polars as pl

import alascraper as a


def test_butterflies_expands_to_exact_six_families() -> None:
    assert a.resolve_cli_families(butterflies=True, family_values=None) == list(
        a.BUTTERFLY_FAMILIES
    )


def test_family_cli_parses_repeated_and_comma_separated_values() -> None:
    args = a.parse_args(
        [
            "--family",
            "Nymphalidae,Lycaenidae",
            "--family",
            "Pieridae",
        ]
    )

    assert a.resolve_cli_families(
        butterflies=args.butterflies,
        family_values=args.family,
    ) == ["Nymphalidae", "Lycaenidae", "Pieridae"]


def test_family_scoped_target_params_include_country_order_and_family() -> None:
    params = a.build_family_target_params("Lepidoptera", "Nymphalidae", "species")

    assert ("q", "*:*") in params
    assert ("fq", 'country:"Australia"') in params
    assert ("fq", 'order:"Lepidoptera"') in params
    assert ("fq", 'family:"Nymphalidae"') in params
    assert ("facets", "species") in params


def test_family_target_filters_include_country_order_family_and_facet() -> None:
    target = a.SpeciesTarget(
        key="danaus_plexippus",
        scientific_name="Danaus plexippus",
        source_order="Lepidoptera",
        source_family="Nymphalidae",
        facet_fq_filters=('species:"Danaus plexippus"',),
    )

    assert a.build_query(target) == "*:*"
    assert a.build_fq_filters(target) == [
        'country:"Australia"',
        'order:"Lepidoptera"',
        'family:"Nymphalidae"',
        'species:"Danaus plexippus"',
    ]


def test_family_parquet_path_uses_class_order_family() -> None:
    assert a.family_parquet_path("Lepidoptera", "insecta", "Nymphalidae") == (
        a.DATASETS_ROOT
        / "insecta"
        / "lepidoptera"
        / "nymphalidae"
        / "nymphalidae.parquet"
    )


def test_family_metadata_contains_query_config_fetch_and_dedupe_fields(
    isolated_outputs: Path,
    target: a.SpeciesTarget,
) -> None:
    family = "Nymphalidae"
    a.configure_family_output_root(isolated_outputs / "nymphalidae")
    merge_stats = a.MergeStats(
        output_path=a.FINAL_ALL_SPECIES_PARQUET,
        input_rows=3,
        output_rows=2,
        dropped_rows=1,
    )
    species_result = a.SpeciesResult(
        species_key=target.key,
        scientific_name=target.scientific_name,
        common_name=target.common_name,
        taxon_lsid=target.taxon_lsid,
        config_fingerprint="fp",
        reported_total_records=3,
        pages_written=1,
        rows_written=3,
        elapsed_seconds=0.1,
        species_parquet_path=a.species_parquet_path(target),
    )

    metadata_path = a.write_family_metadata(
        order="Lepidoptera",
        dataset_class="insecta",
        family=family,
        run_started_utc="2026-05-22T00:00:00+00:00",
        elapsed_seconds=0.2,
        species_targets=[target],
        species_results=[species_result],
        merge_stats=merge_stats,
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["target_generation"]["query"] == "*:*"
    assert metadata["target_generation"]["fq_filters"] == [
        'country:"Australia"',
        'order:"Lepidoptera"',
        'family:"Nymphalidae"',
    ]
    assert metadata["target_generation"]["facet_fields"] == ["species", "subspecies"]
    assert metadata["generated_target_count"] == 1
    assert metadata["records_reported"] == 3
    assert metadata["rows_fetched"] == 3
    assert metadata["rows_kept_after_dedupe"] == 2
    assert metadata["rows_dropped"] == 1
    assert metadata["output_parquet_filename"] == "nymphalidae.parquet"
    assert "config_fingerprint" in metadata
    assert metadata["privacy_settings"]["include_user_data_fields"] is False


def test_family_workflow_removes_scratch_and_leaves_only_visible_outputs(
    monkeypatch,
    tmp_path: Path,
    target: a.SpeciesTarget,
    record_row,
) -> None:
    datasets_root = tmp_path / "datasets"
    family = "Nymphalidae"
    family_target = a.SpeciesTarget(
        key=target.key,
        scientific_name=target.scientific_name,
        common_name=target.common_name,
        source_order="Lepidoptera",
        source_family=family,
        facet_fq_filters=('species:"Testus species"',),
    )

    monkeypatch.setattr(a, "DATASETS_ROOT", datasets_root)
    monkeypatch.setattr(a, "generate_family_species_targets", lambda order, family: [family_target])
    monkeypatch.setattr(a, "WORKERS", 1)
    monkeypatch.setattr(a, "REQUEST_SLEEP_SECONDS_BETWEEN_SPECIES", 0)

    def fake_run_parallel_fetch_for_targets(
        targets: list[a.SpeciesTarget],
        _layout: a.WorkerLayout,
    ) -> list[a.SpeciesResult]:
        output = a.species_parquet_path(targets[0])
        output.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            [record_row(targets[0], uuid="u1", event_date=1)],
            schema=a.SCHEMA,
            orient="row",
        ).write_parquet(output)
        (output.parent / "shards").mkdir()
        return [
            a.SpeciesResult(
                species_key=targets[0].key,
                scientific_name=targets[0].scientific_name,
                common_name=targets[0].common_name,
                taxon_lsid=targets[0].taxon_lsid,
                config_fingerprint="fp",
                reported_total_records=1,
                pages_written=1,
                rows_written=1,
                elapsed_seconds=0.1,
                species_parquet_path=output,
            )
        ]

    monkeypatch.setattr(
        a,
        "run_parallel_fetch_for_targets",
        fake_run_parallel_fetch_for_targets,
    )

    assert (
        a.run_family_occurrence_workflow(
            families=[family],
            order="Lepidoptera",
            dataset_class="insecta",
            write_csv=False,
        )
        == 0
    )

    family_dir = datasets_root / "insecta" / "lepidoptera" / "nymphalidae"
    assert (family_dir / "nymphalidae.parquet").exists()
    assert (family_dir / "metadata.json").exists()
    assert (family_dir / "run_log.txt").exists()
    assert not (family_dir / ".scratch").exists()
    assert not (family_dir / "species").exists()
    assert sorted(path.name for path in family_dir.iterdir() if not path.name.startswith(".")) == [
        "metadata.json",
        "nymphalidae.parquet",
        "run_log.txt",
    ]
