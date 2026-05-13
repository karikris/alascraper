from __future__ import annotations

import polars as pl
import pytest

import alascraper as a


@pytest.mark.xfail(strict=True, reason="Known finding: short pages are not rejected yet.")
def test_short_pages_are_retried_then_rejected(monkeypatch, isolated_outputs, target: a.SpeciesTarget) -> None:
    calls = 0

    def fake_fetch_json(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"occurrences": [{"uuid": "only-one"}]}

    monkeypatch.setattr(a, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(a, "MAX_RETRIES", 2)
    task = a.PageTask(target=target, page_index=0, start=0, page_size=2)

    with pytest.raises(RuntimeError, match="partial shard|row-count"):
        a.write_page_shard(task)

    assert calls == 2


@pytest.mark.xfail(strict=True, reason="Known finding: empty pages are not rejected yet.")
def test_empty_pages_are_retried_then_rejected(monkeypatch, isolated_outputs, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "fetch_json", lambda *args, **kwargs: {"occurrences": []})
    monkeypatch.setattr(a, "MAX_RETRIES", 2)
    task = a.PageTask(target=target, page_index=0, start=0, page_size=2)

    with pytest.raises(RuntimeError, match="partial shard|row-count"):
        a.write_page_shard(task)


@pytest.mark.xfail(strict=True, reason="Known finding: cached shards are reused without expected row-count validation.")
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
