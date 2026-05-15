from __future__ import annotations

import time

import alascraper as a


def test_worker_layout_uses_configured_taxon_lane_map() -> None:
    assert a.worker_layout(16) == a.WorkerLayout(16, 8, 2)
    assert a.worker_layout(12) == a.WorkerLayout(12, 6, 2)
    assert a.worker_layout(8) == a.WorkerLayout(8, 8, 1)
    assert a.worker_layout(4) == a.WorkerLayout(4, 4, 1)
    assert a.worker_layout(2) == a.WorkerLayout(2, 2, 1)


def test_worker_layout_falls_back_to_single_taxon_lane() -> None:
    assert a.worker_layout(3) == a.WorkerLayout(3, 1, 3)


def test_parallel_target_fetch_preserves_input_order(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
    lsid_target: a.SpeciesTarget,
) -> None:
    seen_page_workers: list[int | None] = []

    def fake_fetch_one_species(
        current: a.SpeciesTarget,
        *,
        page_workers: int | None = None,
    ) -> a.SpeciesResult:
        seen_page_workers.append(page_workers)

        if current.key == target.key:
            time.sleep(0.01)

        return a.SpeciesResult(
            species_key=current.key,
            scientific_name=current.scientific_name,
            common_name=current.common_name,
            taxon_lsid=current.taxon_lsid,
            config_fingerprint=current.key,
            reported_total_records=1,
            pages_written=1,
            rows_written=1,
            elapsed_seconds=0.1,
            species_parquet_path=a.species_parquet_path(current),
        )

    monkeypatch.setattr(a, "fetch_one_species", fake_fetch_one_species)
    monkeypatch.setattr(a, "REQUEST_SLEEP_SECONDS_BETWEEN_SPECIES", 0)

    results = a.run_parallel_fetch_for_targets(
        [target, lsid_target],
        a.WorkerLayout(total_workers=2, taxon_lanes=2, page_workers_per_taxon=1),
    )

    assert [result.species_key for result in results] == [target.key, lsid_target.key]
    assert seen_page_workers == [1, 1]
