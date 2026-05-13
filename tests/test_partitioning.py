from __future__ import annotations

import pytest

import alascraper as a


@pytest.mark.xfail(strict=True, reason="Known finding: missing facets still fall back to first search-window page.")
def test_missing_year_facets_raise_for_full_runs(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "fetch_year_facet_partitions", lambda _target: [])

    with pytest.raises(RuntimeError, match="no year facets|truncate"):
        a.make_query_partitions(target, a.SEARCH_API_MAX_WINDOW + 1)


@pytest.mark.xfail(strict=True, reason="Known finding: partition totals are logged but not enforced.")
def test_partition_total_mismatch_raises(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(
        a,
        "fetch_year_facet_partitions",
        lambda _target: [
            a.QueryPartition(label="year=2020", extra_fq_filters=("year:2020",), total_records=2_000),
            a.QueryPartition(label="year=2021", extra_fq_filters=("year:2021",), total_records=2_000),
        ],
    )

    with pytest.raises(RuntimeError, match="partition.*total|coverage"):
        a.make_query_partitions(target, a.SEARCH_API_MAX_WINDOW + 1)
