from __future__ import annotations

import pytest

import alascraper as a


def test_default_fields_exclude_user_data() -> None:
    excluded_fields = {
        "recordedBy",
        "collectors",
        "collector",
        "occurrenceID",
        "references",
        "occurrenceDetails",
        "image",
        "images",
        "imageUrl",
        "largeImageUrl",
        "smallImageUrl",
        "thumbnailUrl",
        "imageUrls",
    }

    assert excluded_fields.isdisjoint(a.FIELDS)


def test_raw_page_json_requires_user_data_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a, "WRITE_RAW_PAGE_JSON", True)
    monkeypatch.setattr(a, "INCLUDE_USER_DATA_FIELDS", False)

    with pytest.raises(ValueError, match="raw observer/source/media fields"):
        a.validate_privacy_settings()


def test_privacy_guard_allows_default_minimised_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a, "WRITE_RAW_PAGE_JSON", False)
    monkeypatch.setattr(a, "INCLUDE_USER_DATA_FIELDS", False)

    a.validate_privacy_settings()
