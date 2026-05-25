from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceFetchResult:
    source: str
    jurisdiction: str
    output_path: Path
    metadata_path: Path | None
    row_count: int


class SourceAdapter(Protocol):
    source: str
    jurisdiction: str

    def fetch_occurrences(self, *, output_path: Path) -> SourceFetchResult:
        """Fetch public occurrence records and write canonical occurrence Parquet."""
        ...
