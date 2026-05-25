from __future__ import annotations

import subprocess
import sys


def test_fetch_state_sources_script_can_be_executed_directly() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/fetch_state_sources.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "nsw_bionet" in result.stdout


def test_build_source_coverage_report_script_can_be_executed_directly() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_source_coverage_report.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--source-name" in result.stdout
