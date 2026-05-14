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


def test_oversized_year_partition_splits_by_month(
    monkeypatch,
    target: a.SpeciesTarget,
) -> None:
    year_partition = a.QueryPartition(
        label="year=2023",
        extra_fq_filters=("year:2023",),
        total_records=5_646,
    )

    monkeypatch.setattr(a, "fetch_year_facet_partitions", lambda _target: [year_partition])

    def fake_fetch_facet_partitions(
        _target: a.SpeciesTarget,
        facet_field: str,
        *,
        base_label: str | None = None,
        extra_fq_filters: tuple[str, ...] = (),
    ) -> list[a.QueryPartition]:
        assert facet_field == "month"
        assert base_label == "year=2023"
        assert extra_fq_filters == ("year:2023",)
        return [
            a.QueryPartition(
                label="year=2023;month=1",
                extra_fq_filters=("year:2023", "month:1"),
                total_records=3_000,
            ),
            a.QueryPartition(
                label="year=2023;month=2",
                extra_fq_filters=("year:2023", "month:2"),
                total_records=2_646,
            ),
        ]

    monkeypatch.setattr(a, "fetch_facet_partitions", fake_fetch_facet_partitions)

    partitions = a.make_query_partitions(target, 5_646)

    assert partitions == [
        a.QueryPartition(
            label="year=2023;month=1",
            extra_fq_filters=("year:2023", "month:1"),
            total_records=3_000,
        ),
        a.QueryPartition(
            label="year=2023;month=2",
            extra_fq_filters=("year:2023", "month:2"),
            total_records=2_646,
        ),
    ]


def test_oversized_month_partition_splits_by_day(
    monkeypatch,
    target: a.SpeciesTarget,
) -> None:
    year_partition = a.QueryPartition(
        label="year=2001",
        extra_fq_filters=("year:2001",),
        total_records=6_000,
    )

    monkeypatch.setattr(a, "fetch_year_facet_partitions", lambda _target: [year_partition])

    def fake_fetch_facet_partitions(
        _target: a.SpeciesTarget,
        facet_field: str,
        *,
        base_label: str | None = None,
        extra_fq_filters: tuple[str, ...] = (),
    ) -> list[a.QueryPartition]:
        if facet_field == "month":
            assert base_label == "year=2001"
            return [
                a.QueryPartition(
                    label="year=2001;month=January",
                    extra_fq_filters=("year:2001", "month:January"),
                    total_records=5_319,
                ),
                a.QueryPartition(
                    label="year=2001;month=February",
                    extra_fq_filters=("year:2001", "month:February"),
                    total_records=681,
                ),
            ]

        assert facet_field == "day"
        assert base_label == "year=2001;month=January"
        assert extra_fq_filters == ("year:2001", "month:January")
        return [
            a.QueryPartition(
                label="year=2001;month=January;day=1",
                extra_fq_filters=("year:2001", "month:January", "day:1"),
                total_records=2_500,
            ),
            a.QueryPartition(
                label="year=2001;month=January;day=2",
                extra_fq_filters=("year:2001", "month:January", "day:2"),
                total_records=2_819,
            ),
        ]

    monkeypatch.setattr(a, "fetch_facet_partitions", fake_fetch_facet_partitions)

    partitions = a.make_query_partitions(target, 6_000)

    assert partitions == [
        a.QueryPartition(
            label="year=2001;month=January;day=1",
            extra_fq_filters=("year:2001", "month:January", "day:1"),
            total_records=2_500,
        ),
        a.QueryPartition(
            label="year=2001;month=January;day=2",
            extra_fq_filters=("year:2001", "month:January", "day:2"),
            total_records=2_819,
        ),
        a.QueryPartition(
            label="year=2001;month=February",
            extra_fq_filters=("year:2001", "month:February"),
            total_records=681,
        ),
    ]


def test_oversized_day_partition_falls_back_to_lat_long(
    monkeypatch,
    target: a.SpeciesTarget,
) -> None:
    year_partition = a.QueryPartition(
        label="year=2001",
        extra_fq_filters=("year:2001",),
        total_records=5_319,
    )

    monkeypatch.setattr(a, "fetch_year_facet_partitions", lambda _target: [year_partition])

    def fake_fetch_facet_partitions(
        _target: a.SpeciesTarget,
        facet_field: str,
        *,
        base_label: str | None = None,
        extra_fq_filters: tuple[str, ...] = (),
        facet_limit: int = a.YEAR_FACET_LIMIT,
    ) -> list[a.QueryPartition]:
        if facet_field == "month":
            return [
                a.QueryPartition(
                    label="year=2001;month=January",
                    extra_fq_filters=("year:2001", "month:January"),
                    total_records=5_319,
                )
            ]

        if facet_field == "day":
            return [
                a.QueryPartition(
                    label="year=2001;month=January;day=1",
                    extra_fq_filters=("year:2001", "month:January", "day:1"),
                    total_records=5_319,
                )
            ]

        assert facet_field == "lat_long"
        assert facet_limit == 5_319
        assert base_label == "year=2001;month=January"
        assert extra_fq_filters == ("year:2001", "month:January")
        return [
            a.QueryPartition(
                label="year=2001;month=January;lat_long=-12,131",
                extra_fq_filters=("year:2001", "month:January", "lat_long:-12,131"),
                total_records=3_000,
            ),
            a.QueryPartition(
                label="year=2001;month=January;lat_long=-13,132",
                extra_fq_filters=("year:2001", "month:January", "lat_long:-13,132"),
                total_records=2_319,
            ),
        ]

    monkeypatch.setattr(a, "fetch_facet_partitions", fake_fetch_facet_partitions)

    partitions = a.make_query_partitions(target, 5_319)

    assert partitions == [
        a.QueryPartition(
            label="year=2001;month=January;lat_long=-12,131",
            extra_fq_filters=("year:2001", "month:January", "lat_long:-12,131"),
            total_records=3_000,
        ),
        a.QueryPartition(
            label="year=2001;month=January;lat_long=-13,132",
            extra_fq_filters=("year:2001", "month:January", "lat_long:-13,132"),
            total_records=2_319,
        ),
    ]
