# alascraper

Fetch Atlas of Living Australia occurrence records species by species.

The workflow is built for Python 3.14, Polars, DuckDB, and Parquet. It fetches
ALA occurrence records in parallel, writes resumable per-page shards, merges
species outputs with DuckDB, and keeps CSV disabled unless explicitly enabled.
Privacy-sensitive observer, source link, comment, and image fields are excluded
by default.

Primary data source: Atlas of Living Australia. Supplementary sources such as
GBIF or taxon-specific authorities should be used for validation, not as the
sole occurrence dataset.

## Install

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For tests:

```bash
pip install -r requirements-dev.txt
```

Before long runs, edit the `USER_AGENT` and `TARGET_GENERATOR_USER_AGENT`
constants in `constants.py` to use a real project contact address. Do not commit
API keys or secrets.

## Run the Default Workflow

```bash
.venv/bin/python alascraper.py
```

By default, `alascraper.py` refreshes the generated ALA-backed species target
list from `scripts/fetch_by_order.py`, then fetches occurrences for each
species. `scripts/fetch_by_order.py` defaults to Australian ALA records in the
order `Lepidoptera`.

Choose the dataset class folder and order at runtime:

```bash
.venv/bin/python alascraper.py --dataset-class insecta --order Lepidoptera
```

If `--dataset-class` is omitted, the run writes under `datasets/misc/<order>/`.

CSV output is off by default. Add the optional final boolean only when you need
CSV next to Parquet:

```bash
.venv/bin/python alascraper.py --dataset-class insecta --order Lepidoptera TRUE
```

Use `FALSE` or omit the final argument to keep CSV disabled.

For Poales:

```bash
.venv/bin/python alascraper.py --dataset-class monocot --order Poales
```

## Run a Class Workflow

Use `--class` for an ALA taxonomic class facet. For birds, this discovers
Australian ALA orders and families under `class:"Aves"`, then writes
family-level Parquet outputs under `datasets/aves/<order>/<family>/`.

```bash
.venv/bin/python alascraper.py --class Aves
```

Use `--dataset-class` only when you want to override the top-level output folder.

## Run Family Outputs

Use `--family` to fetch one or more ALA family facets into family-level Parquet
outputs. Values may be repeated or comma-separated.

```bash
.venv/bin/python alascraper.py --dataset-class insecta --order Lepidoptera --family Nymphalidae
.venv/bin/python alascraper.py --dataset-class insecta --order Lepidoptera --family Nymphalidae,Lycaenidae
```

Use `--butterflies` for the six Australian butterfly families under
Lepidoptera: Hesperiidae, Papilionidae, Pieridae, Nymphalidae, Riodinidae, and
Lycaenidae.

```bash
.venv/bin/python alascraper.py --dataset-class insecta --order Lepidoptera --butterflies
```

## Generate Targets for Another Order

Run the target generator directly when you want to build a reusable target list
for another ALA order before running the occurrence scraper.

```bash
.venv/bin/python scripts/fetch_by_order.py \
  --order Neuroptera \
  --output-dir datasets/insecta/neuroptera
.venv/bin/python alascraper.py --dataset-class insecta --order Neuroptera
```

The generator writes order-specific review files such as
`datasets/insecta/neuroptera/neuroptera_species.csv` and
`datasets/insecta/neuroptera/neuroptera_species.json`. `alascraper.py` reads
the order-specific JSON file directly.

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

Edit these in `constants.py` for normal runs:

```python
DEFAULT_GENERATED_ORDER = "Lepidoptera"
REFRESH_SPECIES_TARGETS_BEFORE_RUN = True
COUNTRY_FILTER_ENABLED = True
COUNTRY_FILTER = 'country:"Australia"'
PAGE_SIZE = 500
WORKERS = 12
TAXON_LANE_LAYOUTS = {
    16: (8, 2),
    12: (6, 2),
    8: (8, 1),
    4: (4, 1),
    2: (2, 1),
}
MAX_RECORDS_PER_SPECIES = None
WRITE_CSV = False
WRITE_DUCKDB_DATABASE = True
WRITE_RAW_PAGE_JSON = False
INCLUDE_USER_DATA_FIELDS = False
FRESH_RUN = False
DATASETS_ROOT = Path("datasets")
```

`WORKERS = 12` is the default for the Ultra 7 265K target machine. The matching
layout runs six taxa at a time with two page workers per taxon. Set
`MAX_RECORDS_PER_SPECIES` to a small number for smoke tests; leave it as `None`
for full species exports.

`TAXON_LANE_LAYOUTS` maps total workers to `(concurrent_taxa, page_workers_per_taxon)`.

## Outputs

Default order-level dataset output directory:

```text
datasets/<class_or_misc>/<order>/
├── ala_species_records.parquet
├── ala_species_records.csv          # only when WRITE_CSV = True
├── ala_species_records.duckdb
├── <order>_species.csv
├── <order>_species.json
├── run_log.txt
├── species_manifest.csv
└── species/
    └── <species_key>/
        ├── <species_key>.parquet
        ├── run_metadata.json
        └── shards/
            └── page_000000.parquet
```

Family-level runs write compact family outputs:

```text
datasets/<class_or_misc>/<order>/<family>/
├── <family>.parquet
├── metadata.json
└── run_log.txt
```

## State Source Adapters

State-source fetches are separate from the stable ALA workflow. They write
source-specific Parquet and an impact report first; `ala_species_records.parquet`
is not changed by these scripts.

NSW BioNet is the first public adapter. It fetches public BioNet Species
Sightings records for the six Australian butterfly families and reports the
expected harmonised-table effect against the current ALA Parquet:

```bash
.venv/bin/python scripts/fetch_state_sources.py --source nsw_bionet
```

For smoke tests, cap the fetch:

```bash
.venv/bin/python scripts/fetch_state_sources.py --source nsw_bionet --limit 100
```

Outputs:

```text
datasets/insecta/lepidoptera/nsw_bionet/
├── nsw_bionet_occurrences.parquet
├── metadata.json
└── nsw_bionet_impact_report.json
```

The impact report estimates candidate duplicate and candidate-new source rows
using scientific name, event date, and rounded coordinates. Treat this as a
review queue before building a harmonised occurrence table, not as a destructive
dedupe operation.

## Cleaning + Quality Profiling

Cleaning and quality-inspection scripts live under `scripts/cleaning/`.
Visualisation scripts will live under `scripts/visuals/`.

Profile family-level Parquet outputs one family at a time:

```bash
.venv/bin/python scripts/cleaning/profile_family_parquet.py --class Aves
```

The profiler discovers files shaped like
`datasets/<class>/<order>/<family>/<family>.parquet`, writes reports under each
family folder, prints the report paths, prompts for notes about that family, and
then moves to the next family.

Filter by order or family:

```bash
.venv/bin/python scripts/cleaning/profile_family_parquet.py --order Lepidoptera
.venv/bin/python scripts/cleaning/profile_family_parquet.py --family Nymphalidae
```

Run without notes prompts for batch/test use:

```bash
.venv/bin/python scripts/cleaning/profile_family_parquet.py --class Aves --no-interactive
```

Enrich the cleaned butterfly parquet with EPBC and state/territory
conservation-status fields:

```bash
.venv/bin/python scripts/cleaning/enrich_butterfly_conservation_status.py
```

The enrichment script reads
`datasets/insecta/lepidoptera/butterflies_cleaned.parquet`, applies the curated
reference table in `data/reference/butterfly_conservation_status.csv`, and
writes:

```text
datasets/insecta/lepidoptera/butterflies_conservation.parquet
datasets/insecta/lepidoptera/quality_reports/butterflies_conservation_status_report.json
```

`Status` stores the EPBC Act status. State and territory listings are kept in
`state_status` and companion source/provenance columns. Matching is exact on
`scientificName`, then exact on `species`, then against synonyms listed in the
reference table.

Per-family report outputs:

```text
datasets/<class>/<order>/<family>/quality_reports/
├── <family>_quality_summary.json
├── <family>_column_profile.csv
├── <family>_categorical_top_values.csv
└── family_notes.md                  # only when notes are entered
```

Join the six Australian butterfly family outputs into one Parquet dataset and
write the same quality-report shape for the joined output:

```bash
.venv/bin/python scripts/cleaning/join_butterfly_families.py --overwrite
```

Joined butterfly outputs:

```text
datasets/insecta/lepidoptera/
├── butterflies.parquet
├── butterflies_metadata.json
└── quality_reports/
    ├── butterflies_quality_summary.json
    ├── butterflies_column_profile.csv
    └── butterflies_categorical_top_values.csv
```

Complete nullable taxonomy fields before dashboard work. The `species` column is
the canonical species-level slicer; `scientificName` and `taxonConceptID` remain
species/subspecies detail fields.

```bash
.venv/bin/python scripts/cleaning/complete_taxonomy_fields.py --no-external-lookup
```

Omit `--no-external-lookup` to allow guarded GBIF species-match fallback for
unresolved names. The script writes `butterflies_cleaned.parquet`, unresolved
taxonomy rows, and month value counts.

Build rounded-coordinate spatial bins for the dashboard. The default rounds
coordinates to whole degrees for a national-scale overview; pass
`--grid-decimals 1` for finer roughly 0.1-degree bins.

```bash
.venv/bin/python scripts/visuals/spatial_heatmap_dashboard/build_spatial_bins.py
```

Run the local dashboard after installing requirements:

```bash
.venv/bin/streamlit run scripts/visuals/spatial_heatmap_dashboard/dashboard.py
```

The dashboard uses central cross-filtering slicers for family, species, year
range, and state/territory include/exclude selections. The default map source is
the pre-aggregated grid Parquet, not the raw occurrence rows. It reports both
visible records and total matching mapped records so map-point caps are explicit.

## Privacy Defaults

`INCLUDE_USER_DATA_FIELDS = False` omits observer/user and source-linked fields.
`WRITE_RAW_PAGE_JSON = False` avoids storing raw ALA responses.

Do not enable these unless ethics approval explicitly covers storing usernames,
profile/source links, comments, or media metadata.

## Validation

Validate occurrence datasets against ALA, GBIF, and taxon-specific authority
lists where needed. Use supplementary sources mainly to detect range extensions,
recent spread, and under-sampled localities.

## Test

Run the unit tests before committing changes:

```bash
.venv/bin/python -m pytest -q
```

## Ownership

Authored by Kris Kari.

Owned by the Global Change Ecology Lab, School of Biological Sciences, Monash
University.

Lab website: <https://shawanchowdhurylab.com/>

## Licence

See `LICENSE`.
