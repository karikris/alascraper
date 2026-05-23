from __future__ import annotations

import csv
import json
from pathlib import Path

import polars as pl

from scripts.cleaning import profile_family_parquet as profiler


def write_family_parquet(
    root: Path,
    *,
    class_key: str = "aves",
    order_key: str = "psittaciformes",
    family_key: str = "psittacidae",
) -> Path:
    family_dir = root / class_key / order_key / family_key
    family_dir.mkdir(parents=True)
    path = family_dir / f"{family_key}.parquet"
    pl.DataFrame(
        {
            "uuid": ["u1", "u1", "u3", None],
            "scientificName": [
                "Testus alpha",
                "Testus alpha",
                "Testus beta",
                None,
            ],
            "species": ["Testus alpha", "Testus alpha", "Testus beta", None],
            "family": ["Psittacidae", "Psittacidae", "Psittacidae", "Psittacidae"],
            "order": [
                "Psittaciformes",
                "Psittaciformes",
                "Psittaciformes",
                "Psittaciformes",
            ],
            "decimalLatitude": [-37.0, -38.0, None, 95.0],
            "decimalLongitude": [144.0, 145.0, 146.0, 181.0],
            "year": [2020, 2021, 2021, None],
            "basisOfRecord": [
                "HUMAN_OBSERVATION",
                "HUMAN_OBSERVATION",
                "PRESERVED_SPECIMEN",
                None,
            ],
        }
    ).write_parquet(path)
    return path


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_discovers_only_family_level_parquets(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    family_path = write_family_parquet(root)
    family_root = root / "aves" / "psittaciformes" / "psittacidae"
    scratch = family_root / ".scratch" / "tmp.parquet"
    shard = family_root / "species" / "x" / "x.parquet"
    unrelated = family_root / "quality_reports" / "x.parquet"
    for path in (scratch, shard, unrelated):
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"x": [1]}).write_parquet(path)

    matches = profiler.discover_family_parquets(root)

    assert [match.path for match in matches] == [family_path]
    assert matches[0].class_key == "aves"
    assert matches[0].order_key == "psittaciformes"
    assert matches[0].family_key == "psittacidae"


def test_discovery_filters_by_class_order_and_family(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    bird_path = write_family_parquet(root)
    write_family_parquet(
        root,
        class_key="insecta",
        order_key="lepidoptera",
        family_key="nymphalidae",
    )

    assert [
        match.path
        for match in profiler.discover_family_parquets(root, taxon_class="Aves")
    ] == [bird_path]
    assert [
        match.family_key
        for match in profiler.discover_family_parquets(root, order="Psittaciformes")
    ] == ["psittacidae"]
    assert [
        match.family_key
        for match in profiler.discover_family_parquets(root, family="Psittacidae")
    ] == ["psittacidae"]


def test_profiles_nulls_distincts_stats_species_counts_and_quality_flags(
    tmp_path: Path,
) -> None:
    path = write_family_parquet(tmp_path / "datasets")

    report = profiler.profile_parquet(path)

    assert report.summary["row_count"] == 4
    assert report.summary["column_count"] == 9
    assert report.summary["duplicate_uuid_count"] == 1
    assert report.summary["coordinate_issue_count"] == 2
    assert report.species_counts == {"Testus alpha": 2, "Testus beta": 1}
    assert "coordinate_range_issue" in report.quality_flags

    species_column = next(
        row for row in report.column_profile if row["column"] == "scientificName"
    )
    assert species_column["null_count"] == 1
    assert species_column["distinct_count"] == 3

    latitude_stats = next(
        row for row in report.numeric_stats if row["column"] == "decimalLatitude"
    )
    assert latitude_stats["min"] == -38.0
    assert latitude_stats["max"] == 95.0


def test_writes_reports_and_appends_guided_notes(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    path = write_family_parquet(root)
    match = profiler.FamilyParquet(
        class_key="aves",
        order_key="psittaciformes",
        family_key="psittacidae",
        path=path,
    )

    outputs = profiler.write_report_files(match, profiler.profile_parquet(path))
    profiler.append_family_note(match, outputs.notes_path, "Needs coordinate review")

    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert summary["family_key"] == "psittacidae"
    assert summary["summary"]["row_count"] == 4
    assert outputs.column_profile_csv.exists()
    assert outputs.numeric_stats_csv.exists()
    assert outputs.categorical_top_values_csv.exists()
    assert "Needs coordinate review" in outputs.notes_path.read_text(encoding="utf-8")

    rows = read_csv_rows(outputs.column_profile_csv)
    assert {row["column"] for row in rows} >= {"uuid", "scientificName"}


def test_main_no_interactive_writes_reports_without_notes_prompt(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    write_family_parquet(root)

    exit_code = profiler.main(
        ["--dataset-root", str(root), "--class", "Aves", "--no-interactive"]
    )

    report_root = root / "aves" / "psittaciformes" / "psittacidae" / "quality_reports"
    assert exit_code == 0
    assert (report_root / "psittacidae_quality_summary.json").exists()
    assert not (report_root / "family_notes.md").exists()


def test_process_families_interactive_collects_mocked_notes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "datasets"
    path = write_family_parquet(root)
    match = profiler.FamilyParquet(
        class_key="aves",
        order_key="psittaciformes",
        family_key="psittacidae",
        path=path,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "Review duplicate UUIDs")

    exit_code = profiler.process_families([match], interactive=True)

    notes = (
        root
        / "aves"
        / "psittaciformes"
        / "psittacidae"
        / "quality_reports"
        / "family_notes.md"
    )
    assert exit_code == 0
    assert "Review duplicate UUIDs" in notes.read_text(encoding="utf-8")


def test_empty_parquet_is_profiled_without_crashing(tmp_path: Path) -> None:
    path = (
        tmp_path
        / "datasets"
        / "aves"
        / "psittaciformes"
        / "psittacidae"
        / "psittacidae.parquet"
    )
    path.parent.mkdir(parents=True)
    pl.DataFrame(
        schema={
            "uuid": pl.Utf8,
            "scientificName": pl.Utf8,
            "decimalLatitude": pl.Float64,
        }
    ).write_parquet(path)

    report = profiler.profile_parquet(path)

    assert report.summary["row_count"] == 0
    assert report.species_counts == {}
    assert any(row["column"] == "uuid" for row in report.column_profile)
