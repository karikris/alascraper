from __future__ import annotations

from pathlib import Path
from typing import Callable

import polars as pl
import pytest

import alascraper as a


@pytest.fixture
def target() -> a.SpeciesTarget:
    return a.SpeciesTarget(
        key="test_species",
        scientific_name="Testus species",
        common_name="Test taxon",
        taxon_lsid=None,
    )


@pytest.fixture
def lsid_target() -> a.SpeciesTarget:
    return a.SpeciesTarget(
        key="lsid_species",
        scientific_name="Lsidus species",
        common_name="LSID taxon",
        taxon_lsid="urn:lsid:biodiversity.org.au:afd.taxon:test",
    )


@pytest.fixture
def isolated_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "outputs" / "ala_species_records"
    monkeypatch.setattr(a, "OUTPUT_ROOT", root)
    monkeypatch.setattr(a, "SPECIES_ROOT", root / "species")
    monkeypatch.setattr(a, "FINAL_ALL_SPECIES_PARQUET", root / "ala_species_records.parquet")
    monkeypatch.setattr(a, "FINAL_ALL_SPECIES_CSV", root / "ala_species_records.csv")
    monkeypatch.setattr(a, "DUCKDB_PATH", root / "ala_species_records.duckdb")
    monkeypatch.setattr(a, "RUN_LOG_PATH", root / "run_log.txt")
    monkeypatch.setattr(a, "MANIFEST_PATH", root / "species_manifest.csv")
    return root


def make_record_row(
    target: a.SpeciesTarget,
    *,
    uuid: str,
    event_date: int | None = None,
    scientific_name: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {field: None for field in a.FIELDS}
    row.update(
        {
            "query_species_key": target.key,
            "query_scientific_name": target.scientific_name,
            "query_common_name": target.common_name,
            "query_taxon_lsid": target.taxon_lsid,
            "uuid": uuid,
            "scientificName": scientific_name or target.scientific_name,
            "eventDate": event_date,
        }
    )
    return row


@pytest.fixture
def record_row() -> Callable[..., dict[str, object]]:
    return make_record_row


def write_shard(target: a.SpeciesTarget, page_index: int, rows: list[dict[str, object]]) -> Path:
    path = a.shard_path(target, page_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=a.SCHEMA, orient="row").write_parquet(path)
    return path


@pytest.fixture
def parquet_shard_writer() -> Callable[[a.SpeciesTarget, int, list[dict[str, object]]], Path]:
    return write_shard
