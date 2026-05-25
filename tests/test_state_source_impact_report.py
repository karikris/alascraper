from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from schemas.occurrence import CANONICAL_OCCURRENCE_SCHEMA
from scripts import build_source_coverage_report as report


def test_build_impact_report_counts_candidate_duplicates_and_new_records(
    tmp_path: Path,
) -> None:
    ala_path = tmp_path / "ala_species_records.parquet"
    source_path = tmp_path / "nsw_bionet.parquet"
    report_path = tmp_path / "impact_report.json"

    pl.DataFrame(
        [
            {
                "uuid": "ala-1",
                "scientificName": "Toxidia peron",
                "family": "Hesperiidae",
                "stateProvince": "NSW",
                "decimalLatitude": -28.3952072,
                "decimalLongitude": 153.2746444,
                "eventDate_iso": "2008-10-01T00:00:00+00:00",
            },
            {
                "uuid": "ala-2",
                "scientificName": "Junonia villida",
                "family": "Nymphalidae",
                "stateProvince": "NSW",
                "decimalLatitude": -33.0,
                "decimalLongitude": 151.0,
                "eventDate_iso": "2020-01-01T00:00:00+00:00",
            },
        ]
    ).write_parquet(ala_path)

    source_rows = []
    for record_id, name, family, lat, lon, date in [
        ("bio-1", "Toxidia peron", "Hesperiidae", -28.3952072, 153.2746444, "2008-10-01"),
        ("bio-2", "Candalides absimilis", "Lycaenidae", -32.1, 150.2, "2022-03-05"),
    ]:
        row = {column: None for column in CANONICAL_OCCURRENCE_SCHEMA}
        row.update(
            {
                "source": "nsw_bionet",
                "source_jurisdiction": "NSW",
                "source_record_id": record_id,
                "scientific_name": name,
                "family": family,
                "state_province": "NSW",
                "decimal_latitude": lat,
                "decimal_longitude": lon,
                "event_date_start": date,
                "year": int(date[:4]),
            }
        )
        source_rows.append(row)

    pl.DataFrame(
        source_rows,
        schema=CANONICAL_OCCURRENCE_SCHEMA,
        orient="row",
    ).write_parquet(source_path)

    impact = report.build_impact_report(
        ala_path=ala_path,
        source_path=source_path,
        output_path=report_path,
        source_name="nsw_bionet",
        families=("Hesperiidae", "Lycaenidae", "Nymphalidae"),
    )

    assert impact["existing_ala_rows_considered"] == 2
    assert impact["source_rows"] == 2
    assert impact["candidate_duplicate_rows"] == 1
    assert impact["candidate_new_rows"] == 1
    assert impact["expected_harmonised_rows_after_candidate_dedupe"] == 3
    assert impact["existing_ala_table_changed"] is False
    assert "does not mutate" in impact["expected_effect"]

    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written == impact


def test_impact_report_handles_missing_ala_table(tmp_path: Path) -> None:
    source_path = tmp_path / "nsw_bionet.parquet"
    row = {column: None for column in CANONICAL_OCCURRENCE_SCHEMA}
    row.update(
        {
            "source": "nsw_bionet",
            "source_jurisdiction": "NSW",
            "source_record_id": "bio-1",
            "scientific_name": "Toxidia peron",
            "family": "Hesperiidae",
        }
    )
    pl.DataFrame([row], schema=CANONICAL_OCCURRENCE_SCHEMA, orient="row").write_parquet(
        source_path
    )

    impact = report.build_impact_report(
        ala_path=tmp_path / "missing.parquet",
        source_path=source_path,
        output_path=None,
        source_name="nsw_bionet",
        families=("Hesperiidae",),
    )

    assert impact["ala_table_found"] is False
    assert impact["source_rows"] == 1
    assert impact["candidate_duplicate_rows"] == 0
    assert impact["candidate_new_rows"] == 1


def test_impact_report_handles_unreadable_ala_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ala_path = tmp_path / "ala_species_records.parquet"
    source_path = tmp_path / "nsw_bionet.parquet"
    ala_path.write_text("not real parquet", encoding="utf-8")
    row = {column: None for column in CANONICAL_OCCURRENCE_SCHEMA}
    row.update(
        {
            "source": "nsw_bionet",
            "source_jurisdiction": "NSW",
            "source_record_id": "bio-1",
            "scientific_name": "Toxidia peron",
            "family": "Hesperiidae",
        }
    )
    source_frame = pl.DataFrame(
        [row],
        schema=CANONICAL_OCCURRENCE_SCHEMA,
        orient="row",
    )
    source_frame.write_parquet(source_path)

    def fake_read_ala_summary(path: Path, families: tuple[str, ...]) -> dict[str, object]:
        assert path == ala_path
        assert families == ("Hesperiidae",)
        raise BaseException("simulated parquet reader panic")

    monkeypatch.setattr(report, "read_ala_summary", fake_read_ala_summary)

    impact = report.build_impact_report(
        ala_path=ala_path,
        source_path=source_path,
        output_path=None,
        source_name="nsw_bionet",
        families=("Hesperiidae",),
    )

    assert impact["ala_table_found"] is True
    assert impact["ala_table_readable"] is False
    assert impact["ala_read_error"] == "simulated parquet reader panic"
    assert impact["candidate_new_rows"] == 1
