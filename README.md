# alascraper

Fetch Atlas of Living Australia occurrence records species by species.

The default workflow writes Parquet outputs, uses DuckDB for merging, and keeps
CSV disabled unless you explicitly enable it. Privacy-sensitive observer, source
link, comment, and image fields are excluded by default.

## Install

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the Default Workflow

```bash
.venv/bin/python alascraper.py
```

By default, `alascraper.py` refreshes the generated ALA-backed species target
list from `fetch_australian_butterfly_species.py`, then fetches occurrences for
each species.

## Call a Species Directly

Use `run_alascraper()` when you want to fetch one species or provide your own
species list without editing generated files.

```bash
.venv/bin/python -c "import alascraper as a; raise SystemExit(a.run_alascraper(species_targets=[{'key': 'papilio_aegeus', 'scientific_name': 'Papilio aegeus'}], write_csv=False, refresh_generated_targets=False))"
```

Prefer an ALA taxon LSID when you have one:

```bash
.venv/bin/python -c "import alascraper as a; raise SystemExit(a.run_alascraper(species_targets=[{'key': 'papilio_aegeus', 'scientific_name': 'Papilio aegeus', 'taxon_lsid': 'ALA_TAXON_LSID_HERE'}], write_csv=False, refresh_generated_targets=False))"
```

For repeated use, pass a list:

```python
import alascraper as a

targets = [
    {
        "key": "papilio_aegeus",
        "scientific_name": "Papilio aegeus",
        "common_name": "Orchard Swallowtail",
    },
    {
        "key": "danaus_plexippus",
        "scientific_name": "Danaus plexippus",
        "common_name": "Monarch",
    },
]

raise SystemExit(
    a.run_alascraper(
        species_targets=targets,
        write_csv=False,
        refresh_generated_targets=False,
    )
)
```

`key` should be short, lowercase, filesystem-safe, and unique.

## Useful Constants

Edit these in `alascraper.py` for normal runs:

```python
WORKERS = 12
MAX_RECORDS_PER_SPECIES = None
WRITE_CSV = False
FRESH_RUN = False
OUTPUT_ROOT = Path("outputs") / "ala_species_records"
```

Set `MAX_RECORDS_PER_SPECIES` to a small number for test runs. Leave it as
`None` for full species exports.

## Outputs

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
        ├── <species_key>.parquet
        ├── run_metadata.json
        └── shards/
            └── page_000000.parquet
```

## Privacy Defaults

`INCLUDE_USER_DATA_FIELDS = False` omits observer/user and source-linked fields.
`WRITE_RAW_PAGE_JSON = False` avoids storing raw ALA responses.

Do not enable these unless ethics approval explicitly covers storing usernames,
profile/source links, comments, or media metadata.

## Validation

Validate occurrence datasets against ALA, GBIF, and taxon-specific authority
lists where needed. Use supplementary sources mainly to detect range extensions,
recent spread, and under-sampled localities.

## Ownership

Authored by Kris Kari.

Owned by the Global Change Ecology Lab, School of Biological Sciences, Monash
University.

Lab website: <https://shawanchowdhurylab.com/>

## Licence

See `LICENSE`.
