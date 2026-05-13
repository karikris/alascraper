# alascraper

`alascraper` is a Python 3.14 workflow for fetching Australian butterfly occurrence records from the Atlas of Living Australia (ALA) species by species. It is designed for reproducible ecological data collection with fast parallel fetching, privacy-conscious default outputs, and efficient local storage in Parquet, DuckDB, and optional CSV formats.

## Ownership

This project is authored by **Kris Kari** and owned by the **Global Change Ecology Lab, School of Biological Sciences, Monash University**.

Lab website: <https://shawanchowdhurylab.com/>

## Purpose

The script supports Australia-localised butterfly range and spread studies by building occurrence datasets from ALA-backed biodiversity records. The intended baseline data sources are:

- Butterflies Australia records available through ALA
- iNaturalist Australia records available through ALA
- Other ALA biodiversity data providers relevant to Australian butterfly observations

The workflow is species-by-species so the target species list can be edited and reused for different butterfly taxa.

## Current Script

Main entry point:

```bash
python alascraper.py
```

The script currently targets these example species in `SPECIES_TARGETS`:

- `Papilio aegeus` - Orchard Swallowtail
- `Graphium sarpedon` - Blue Triangle
- `Danaus plexippus` - Monarch
- `Pieris rapae` - Cabbage White

Edit `SPECIES_TARGETS` in `alascraper.py` to fetch another species set. Prefer ALA taxon LSIDs where available for stricter taxonomy.

## Features

- Python 3.14 project using `polars`, `duckdb`, `requests`, and Parquet.
- Parallel page fetching with `WORKERS = 12`, suitable for a high-core desktop CPU.
- Thread-local HTTP sessions for connection reuse without sharing a `requests.Session` across worker threads.
- Default Parquet output with optional CSV output via `WRITE_CSV`.
- Species-level and all-species merged outputs.
- Per-species metadata and config fingerprints to prevent stale shard reuse when query, schema, code, or output settings change.
- Normal research runs refresh all species by default with `FRESH_RUN = True`.
- Stable ALA pagination settings plus UUID de-duplication during DuckDB merges.
- Year-facet partitioning for uncapped full-dataset runs, avoiding ALA search-window truncation above 5,000 rows.
- Strict page row-count validation before writing shards; short or empty ALA pages are retried and then rejected instead of stored.
- Run logs and manifest files with elapsed timings and row counts.

## Data Governance Defaults

The script is privacy-minimised by default.

`INCLUDE_USER_DATA_FIELDS = False` excludes observer/user and source-link fields such as:

- `recordedBy`
- `collectors`
- `collector`
- `occurrenceID`
- `references`
- `occurrenceDetails`
- image identifiers and image URLs

`WRITE_RAW_PAGE_JSON = False` is also the default. If raw page JSON is enabled without also enabling user-data fields, the script raises an error because raw ALA responses can contain observer names, source observation links, and media identifiers.

Do not enable these fields unless ethics approval explicitly covers storage of observer/user data and source-linked media metadata.

## Installation

Create and activate a Python 3.14 virtual environment:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The local `.venv/`, generated outputs, DuckDB files, Parquet files, and CSV files are ignored by Git.

## Configuration

Common constants in `alascraper.py`:

```python
SPECIES_TARGETS = [...]
WORKERS = 12
PAGE_SIZE = 500
MAX_RECORDS_PER_SPECIES = None
WRITE_CSV = False
FRESH_RUN = True
OUTPUT_ROOT = Path("outputs") / "ala_species_records"
```

Normal research runs refresh all species by default with `FRESH_RUN = True`. Set it to `False` only when intentionally resuming the same query/config fingerprint.

Capped runs currently support `MAX_RECORDS_PER_SPECIES = None` or values up to `5_000`. Larger caps are rejected until deterministic capped partitioning is implemented.

For a small capped run, set:

```python
MAX_RECORDS_PER_SPECIES = 5_000
WRITE_CSV = True
FRESH_RUN = True
```

For a full run, set:

```python
MAX_RECORDS_PER_SPECIES = None
WRITE_CSV = True
FRESH_RUN = True
```

## Output Layout

Default output directory:

```text
outputs/ala_species_records/
├── ala_species_records.parquet
├── ala_species_records.csv          # only when WRITE_CSV = True
├── ala_species_records.duckdb
├── run_log.txt
├── species_manifest.csv
└── species/
    └── <species_key>/
        ├── run_metadata.json
        ├── <species_key>.parquet
        └── shards/
            └── page_000000.parquet
```

`run_log.txt` records progress, row counts, de-duplication counts, and elapsed durations. `species_manifest.csv` records each species query, filters, row counts, output path, config fingerprint, and elapsed time.

## Example: 5,000-Row-Per-Species CSV Run

From Python, override runtime constants without editing the source file:

```bash
.venv/bin/python -c "import alascraper as a; root=a.Path('outputs') / 'ala_species_records_5000'; a.OUTPUT_ROOT=root; a.SPECIES_ROOT=root / 'species'; a.FINAL_ALL_SPECIES_PARQUET=root / 'ala_species_records.parquet'; a.FINAL_ALL_SPECIES_CSV=root / 'ala_species_records.csv'; a.DUCKDB_PATH=root / 'ala_species_records.duckdb'; a.RUN_LOG_PATH=root / 'run_log.txt'; a.MANIFEST_PATH=root / 'species_manifest.csv'; a.MAX_RECORDS_PER_SPECIES=5000; a.WRITE_CSV=True; a.FRESH_RUN=True; raise SystemExit(a.main())"
```

## Example: Full Dataset CSV Run

```bash
.venv/bin/python -c "import alascraper as a; root=a.Path('outputs') / 'ala_species_records_full'; a.OUTPUT_ROOT=root; a.SPECIES_ROOT=root / 'species'; a.FINAL_ALL_SPECIES_PARQUET=root / 'ala_species_records.parquet'; a.FINAL_ALL_SPECIES_CSV=root / 'ala_species_records.csv'; a.DUCKDB_PATH=root / 'ala_species_records.duckdb'; a.RUN_LOG_PATH=root / 'run_log.txt'; a.MANIFEST_PATH=root / 'species_manifest.csv'; a.MAX_RECORDS_PER_SPECIES=None; a.WRITE_CSV=True; a.FRESH_RUN=True; raise SystemExit(a.main())"
```

## Validation Notes

The script logs when ALA resolves a requested scientific name to a different accepted or normalised taxon name. For strict taxonomic workflows, supply ALA LSIDs in `SPECIES_TARGETS` and validate records against:

- Atlas of Living Australia
- GBIF
- Australian Faunal Directory / Braby taxonomy where needed

## Licence

See `LICENSE` for repository licensing terms.
