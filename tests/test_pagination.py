from __future__ import annotations

import alascraper as a


def test_make_tasks_exact_page_multiples(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "PAGE_SIZE", 500)
    partitions = [a.QueryPartition(label="all", extra_fq_filters=(), total_records=1_000)]

    tasks = a.make_tasks(target, partitions)

    assert [(task.page_index, task.start, task.page_size) for task in tasks] == [
        (0, 0, 500),
        (1, 500, 500),
    ]


def test_make_tasks_final_partial_page(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "PAGE_SIZE", 500)
    partitions = [a.QueryPartition(label="all", extra_fq_filters=(), total_records=1_200)]

    tasks = a.make_tasks(target, partitions)

    assert [(task.page_index, task.start, task.page_size) for task in tasks] == [
        (0, 0, 500),
        (1, 500, 500),
        (2, 1_000, 200),
    ]


def test_make_tasks_multiple_partitions(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "PAGE_SIZE", 500)
    partitions = [
        a.QueryPartition(label="year=2020", extra_fq_filters=("year:2020",), total_records=750),
        a.QueryPartition(label="year=2021", extra_fq_filters=("year:2021",), total_records=250),
    ]

    tasks = a.make_tasks(target, partitions)

    assert [(task.page_index, task.partition_label, task.start, task.page_size) for task in tasks] == [
        (0, "year=2020", 0, 500),
        (1, "year=2020", 500, 250),
        (2, "year=2021", 0, 250),
    ]
    assert tasks[0].extra_fq_filters == ("year:2020",)
    assert tasks[2].extra_fq_filters == ("year:2021",)


def test_make_tasks_zero_records(target: a.SpeciesTarget) -> None:
    partitions = [a.QueryPartition(label="all", extra_fq_filters=(), total_records=0)]

    assert a.make_tasks(target, partitions) == []
