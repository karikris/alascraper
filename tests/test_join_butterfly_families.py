from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

import alascraper as a
from scripts.cleaning import join_butterfly_families as joiner


def record_row(
    *,
    family: str,
    uuid: str,
    scientific_name: str,
    event_date: int,
) -> dict[str, object]:
    row: dict[str, object] = {field: None for field in a.FIELDS}
    row.update(
        {
            "query_species_key": a.safe_key(scientific_name),
            "query_scientific_name": scientific_name,
            "uuid": uuid,
            "scientificName": scientific_name,
            "raw_scientificName": scientific_name,
            "kingdom": "Animalia",
            "classs": "Insecta",
            "order": "Lepidoptera",
            "family": family,
            "species": scientific_name,
            "country": "Australia",
            "decimalLatitude": -37.0,
            "decimalLongitude": 145.0,
            "eventDate": event_date,
            "year": 2024,
            "basisOfRecord": "HUMAN_OBSERVATION",
        }
    )
    return row


def write_family_input(
    root: Path,
    *,
    family: str,
    rows: list[dict[str, object]],
) -> Path:
    family_key = a.safe_key(family)
    path = root / "insecta" / "lepidoptera" / family_key / f"{family_key}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=a.SCHEMA, orient="row").write_parquet(path)
    return path


def write_all_butterfly_inputs(root: Path) -> None:
    rows_by_family = {
        "Hesperiidae": [
            record_row(
                family="Hesperiidae",
                uuid="shared",
                scientific_name="Shared species",
                event_date=2_000,
            )
        ],
        "Lycaenidae": [
            record_row(
                family="Lycaenidae",
                uuid="shared",
                scientific_name="Shared species",
                event_date=1_000,
            )
        ],
        "Papilionidae": [
            record_row(
                family="Papilionidae",
                uuid="papilionidae-1",
                scientific_name="Papilio testus",
                event_date=3_000,
            )
        ],
        "Pieridae": [
            record_row(
                family="Pieridae",
                uuid="pieridae-1",
                scientific_name="Pieris testus",
                event_date=4_000,
            )
        ],
        "Nymphalidae": [
            record_row(
                family="Nymphalidae",
                uuid="nymphalidae-1",
                scientific_name="Danaus testus",
                event_date=5_000,
            )
        ],
        "Riodinidae": [
            record_row(
                family="Riodinidae",
                uuid="riodinidae-1",
                scientific_name="Riodina testus",
                event_date=6_000,
            )
        ],
    }
    for family, rows in rows_by_family.items():
        write_family_input(root, family=family, rows=rows)


def test_builds_expected_six_butterfly_input_paths(tmp_path: Path) -> None:
    root = tmp_path / "datasets"

    paths = joiner.butterfly_family_inputs(root, "insecta", "Lepidoptera")

    assert [path.relative_to(root).as_posix() for path in paths] == [
        "insecta/lepidoptera/hesperiidae/hesperiidae.parquet",
        "insecta/lepidoptera/papilionidae/papilionidae.parquet",
        "insecta/lepidoptera/pieridae/pieridae.parquet",
        "insecta/lepidoptera/nymphalidae/nymphalidae.parquet",
        "insecta/lepidoptera/riodinidae/riodinidae.parquet",
        "insecta/lepidoptera/lycaenidae/lycaenidae.parquet",
    ]


def test_missing_required_family_input_fails_clearly(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    for family in a.BUTTERFLY_FAMILIES[:-1]:
        write_family_input(
            root,
            family=family,
            rows=[
                record_row(
                    family=family,
                    uuid=f"{a.safe_key(family)}-1",
                    scientific_name=f"{family} species",
                    event_date=1_000,
                )
            ],
        )

    with pytest.raises(FileNotFoundError, match="lycaenidae"):
        joiner.join_butterfly_families(
            dataset_root=root,
            dataset_class="insecta",
            order="Lepidoptera",
            overwrite=True,
        )


def test_join_writes_deduped_parquet_metadata_and_quality_reports(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    write_all_butterfly_inputs(root)

    outputs = joiner.join_butterfly_families(
        dataset_root=root,
        dataset_class="insecta",
        order="Lepidoptera",
        overwrite=True,
    )

    df = pl.read_parquet(outputs.output_parquet)
    metadata = json.loads(outputs.metadata_json.read_text(encoding="utf-8"))
    summary = json.loads(outputs.summary_json.read_text(encoding="utf-8"))

    assert outputs.output_parquet == (
        root / "insecta" / "lepidoptera" / "butterflies.parquet"
    )
    assert df.height == 5
    assert df.columns == a.FIELDS
    assert "_uuid_rank" not in df.columns
    assert df.filter(pl.col("uuid") == "shared")["eventDate"].item() == 1_000
    assert metadata["input_rows"] == 6
    assert metadata["output_rows"] == 5
    assert metadata["dropped_rows"] == 1
    assert len(metadata["input_files"]) == 6
    assert summary["dataset_key"] == "butterflies"
    assert summary["source_family_keys"] == [
        "hesperiidae",
        "papilionidae",
        "pieridae",
        "nymphalidae",
        "riodinidae",
        "lycaenidae",
    ]
    assert outputs.column_profile_csv.exists()
    assert outputs.categorical_top_values_csv.exists()
    assert not (outputs.summary_json.parent / "butterflies_numeric_stats.csv").exists()


def test_join_refuses_existing_output_without_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    write_all_butterfly_inputs(root)
    output = root / "insecta" / "lepidoptera" / "butterflies.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"x": [1]}).write_parquet(output)

    with pytest.raises(FileExistsError, match="--overwrite"):
        joiner.join_butterfly_families(
            dataset_root=root,
            dataset_class="insecta",
            order="Lepidoptera",
            overwrite=False,
        )
