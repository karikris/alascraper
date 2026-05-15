from __future__ import annotations

import polars as pl

import alascraper as a


def test_short_pages_are_retried_then_kept_with_partial_status(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
) -> None:
    calls = 0

    def fake_fetch_json(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"occurrences": [{"uuid": "only-one"}]}

    monkeypatch.setattr(a, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(a, "MAX_RETRIES", 2)
    monkeypatch.setattr(a.time, "sleep", lambda _seconds: None)
    task = a.PageTask(target=target, page_index=0, start=0, page_size=2)

    result = a.write_page_shard(task)

    assert calls == 2
    assert result.count == 1
    assert result.validation_status == "partial_row_count"
    assert "expected=2" in (result.validation_detail or "")


def test_empty_pages_are_retried_then_kept_with_partial_status(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
) -> None:
    monkeypatch.setattr(a, "fetch_json", lambda *args, **kwargs: {"occurrences": []})
    monkeypatch.setattr(a, "MAX_RETRIES", 2)
    monkeypatch.setattr(a.time, "sleep", lambda _seconds: None)
    task = a.PageTask(target=target, page_index=0, start=0, page_size=2)

    result = a.write_page_shard(task)

    assert result.count == 0
    assert result.validation_status == "partial_row_count"

def test_cached_shard_with_wrong_row_count_is_refetched(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
    record_row,
    parquet_shard_writer,
) -> None:
    parquet_shard_writer(target, 0, [record_row(target, uuid="stale", event_date=1)])
    called = False

    def fake_fetch_json(*args, **kwargs):
        nonlocal called
        called = True
        return {
            "occurrences": [
                {"uuid": "fresh-1", "eventDate": 1},
                {"uuid": "fresh-2", "eventDate": 2},
            ]
        }

    monkeypatch.setattr(a, "fetch_json", fake_fetch_json)
    task = a.PageTask(target=target, page_index=0, start=0, page_size=2)

    result = a.write_page_shard(task)
    df = pl.read_parquet(result.shard_path)

    assert called is True
    assert result.count == 2
    assert set(df["uuid"].to_list()) == {"fresh-1", "fresh-2"}


def test_parallel_fetch_keeps_failed_page_as_empty_shard(
    monkeypatch,
    isolated_outputs,
    target: a.SpeciesTarget,
) -> None:
    monkeypatch.setattr(a, "WORKERS", 1)
    monkeypatch.setattr(a, "MAX_IN_FLIGHT_TASKS", 1)

    def fail_page(_task: a.PageTask) -> a.PageResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(a, "write_page_shard", fail_page)

    results = a.run_parallel_fetch_for_species(
        target,
        [a.PageTask(target=target, page_index=0, start=0, page_size=1)],
    )

    assert len(results) == 1
    assert results[0].count == 0
    assert results[0].validation_status == "fetch_failed"
    assert results[0].shard_path.exists()
