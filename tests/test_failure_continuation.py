from __future__ import annotations

import alascraper as a


def test_run_continues_after_species_failure(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
    lsid_target: a.SpeciesTarget,
) -> None:
    captured: list[a.SpeciesResult] = []

    monkeypatch.setattr(a, "configure_output_root", lambda _path: None)
    monkeypatch.setattr(a, "validate_privacy_settings", lambda: None)
    monkeypatch.setattr(a, "validate_run_settings", lambda: None)
    monkeypatch.setattr(a, "prepare_output_dirs", lambda: None)
    monkeypatch.setattr(a.time, "sleep", lambda _seconds: None)

    def fake_fetch_one_species(
        current: a.SpeciesTarget,
        *,
        page_workers: int | None = None,
    ) -> a.SpeciesResult:
        assert page_workers == a.worker_layout().page_workers_per_taxon

        if current.key == target.key:
            raise RuntimeError("temporary upstream failure")

        return a.SpeciesResult(
            species_key=current.key,
            scientific_name=current.scientific_name,
            common_name=current.common_name,
            taxon_lsid=current.taxon_lsid,
            config_fingerprint="ok",
            reported_total_records=1,
            pages_written=1,
            rows_written=1,
            elapsed_seconds=0.1,
            species_parquet_path=a.species_parquet_path(current),
        )

    monkeypatch.setattr(a, "fetch_one_species", fake_fetch_one_species)
    monkeypatch.setattr(a, "write_manifest", lambda results, _targets: captured.extend(results))
    monkeypatch.setattr(a, "merge_all_species", lambda _results: None)

    assert a.run_alascraper(species_targets=[target, lsid_target]) == 0
    assert [result.fetch_status for result in captured] == ["failed", "complete"]
    assert "temporary upstream failure" in (captured[0].fetch_error or "")
