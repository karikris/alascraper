from __future__ import annotations

import pytest

import alascraper as a


@pytest.mark.parametrize("cap", [None, 1, 5_000])
def test_supported_caps_pass_validation(monkeypatch, cap: int | None) -> None:
    monkeypatch.setattr(a, "MAX_RECORDS_PER_SPECIES", cap)

    a.validate_run_settings()


def test_cap_above_search_window_raises_before_network(monkeypatch) -> None:
    monkeypatch.setattr(a, "MAX_RECORDS_PER_SPECIES", a.SEARCH_API_MAX_WINDOW + 1)

    with pytest.raises(ValueError, match="MAX_RECORDS_PER_SPECIES"):
        a.validate_run_settings()
