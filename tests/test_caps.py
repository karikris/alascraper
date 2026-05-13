from __future__ import annotations

import pytest

import alascraper as a


@pytest.mark.xfail(strict=True, reason="Known finding: run settings validation does not exist yet.")
@pytest.mark.parametrize("cap", [None, 1, 5_000])
def test_supported_caps_pass_validation(monkeypatch, cap: int | None) -> None:
    monkeypatch.setattr(a, "MAX_RECORDS_PER_SPECIES", cap)

    a.validate_run_settings()


@pytest.mark.xfail(strict=True, reason="Known finding: caps above the search window are not rejected yet.")
def test_cap_above_search_window_raises_before_network(monkeypatch) -> None:
    monkeypatch.setattr(a, "MAX_RECORDS_PER_SPECIES", a.SEARCH_API_MAX_WINDOW + 1)

    with pytest.raises(ValueError, match="MAX_RECORDS_PER_SPECIES"):
        a.validate_run_settings()
