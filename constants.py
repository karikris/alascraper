from __future__ import annotations

from pathlib import Path

import polars as pl


# API and generated-target defaults
API_URL = "https://biocache-ws.ala.org.au/ws/occurrences/search"
DEFAULT_GENERATED_ORDER = "Lepidoptera"
REFRESH_SPECIES_TARGETS_BEFORE_RUN = True

# Query controls
QUALITY_PROFILE = "ALA"
QUALITY_CONTROL = "-_nest_parent_:*"
START_PARAM_NAME = "start"
COUNTRY_FILTER_ENABLED = True
COUNTRY_FILTER = 'country:"Australia"'
EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID = False
EXACT_TAXON_NAME_FILTER_WHEN_LSID_SUPPLIED = False
ALA_SORT_FIELD = "eventDate"
ALA_SORT_DIRECTION = "asc"
DEDUPE_BY_UUID = True
SEARCH_API_MAX_WINDOW = 5_000
YEAR_FACET_LIMIT = 1_000
INVALID_TAXON_LABELS = {
    "not supplied",
    "not provided",
    "not recorded",
    "unknown",
    "unidentified",
    "other values",
}

# Fetch controls
PAGE_SIZE = 500
WORKERS = 16
MAX_IN_FLIGHT_TASKS = WORKERS * 3
TAXON_LANE_LAYOUTS = {
    16: (8, 2),
    12: (6, 2),
    8: (8, 1),
    4: (4, 1),
    2: (2, 1),
}
REQUEST_SLEEP_SECONDS_PER_PAGE = 0.05
REQUEST_SLEEP_SECONDS_BETWEEN_SPECIES = 0.2
MAX_RETRIES = 5
TIMEOUT_SECONDS = 90
HTTP_POOL_CONNECTIONS = WORKERS * 2
HTTP_POOL_MAXSIZE = WORKERS * 2
MAX_RECORDS_PER_SPECIES: int | None = None

# Output controls
WRITE_CSV = False
WRITE_DUCKDB_DATABASE = True
WRITE_RAW_PAGE_JSON = False
INCLUDE_USER_DATA_FIELDS = False
FRESH_RUN = False
DATASETS_ROOT = Path("datasets")
PARQUET_COMPRESSION = "zstd"
PARQUET_COMPRESSION_LEVEL = 3
PARQUET_ROW_GROUP_SIZE = 100_000
USER_AGENT = (
    "Monash-ALA-species-occurrence-research/0.4 "
    "(contact: replace-with-your-email@monash.edu)"
)
RUN_CONFIG_VERSION = 2
RESUME_CACHE_VERSION = 1

# Target generator controls
TARGET_GENERATOR_OUTPUT_DIR = DATASETS_ROOT / "misc" / "lepidoptera"
TARGET_GENERATOR_FACET_FIELDS = ["species", "subspecies"]
TARGET_GENERATOR_FACET_LIMIT = 1_000
TARGET_GENERATOR_REQUEST_SLEEP_SECONDS = 0.3
TARGET_GENERATOR_TIMEOUT_SECONDS = 60
TARGET_GENERATOR_USER_AGENT = (
    "Monash-ALA-order-species-list/0.3 "
    "(contact: replace-with-your-email@monash.edu)"
)

# Output fields
BASE_FIELDS = [
    "query_species_key",
    "query_scientific_name",
    "query_common_name",
    "query_taxon_lsid",
    "target_match_suspect",
    "uuid",
    "scientificName",
    "raw_scientificName",
    "vernacularName",
    "taxonRank",
    "taxonConceptID",
    "kingdom",
    "phylum",
    "classs",
    "order",
    "family",
    "genus",
    "species",
    "country",
    "stateProvince",
    "decimalLatitude",
    "decimalLongitude",
    "coordinateUncertaintyInMeters",
    "spatiallyValid",
    "geospatialKosher",
    "eventDate",
    "eventDate_iso",
    "year",
    "month",
    "day",
    "basisOfRecord",
    "raw_basisOfRecord",
    "dataProviderUid",
    "dataProviderName",
    "dataResourceUid",
    "dataResourceName",
    "license",
    "identificationVerificationStatus",
    "recordNumber",
    "assertions",
    "speciesGroups",
    "latLong",
]

USER_DATA_FIELDS = [
    "occurrenceID",
    "recordedBy",
    "collectors",
    "collector",
    "image",
    "images",
    "imageUrl",
    "largeImageUrl",
    "smallImageUrl",
    "thumbnailUrl",
    "imageUrls",
    "references",
    "occurrenceDetails",
]

FIELDS = BASE_FIELDS + (USER_DATA_FIELDS if INCLUDE_USER_DATA_FIELDS else [])

FIELD_SCHEMA: dict[str, pl.DataType] = {
    "query_species_key": pl.Utf8,
    "query_scientific_name": pl.Utf8,
    "query_common_name": pl.Utf8,
    "query_taxon_lsid": pl.Utf8,
    "target_match_suspect": pl.Boolean,
    "uuid": pl.Utf8,
    "occurrenceID": pl.Utf8,
    "scientificName": pl.Utf8,
    "raw_scientificName": pl.Utf8,
    "vernacularName": pl.Utf8,
    "taxonRank": pl.Utf8,
    "taxonConceptID": pl.Utf8,
    "kingdom": pl.Utf8,
    "phylum": pl.Utf8,
    "classs": pl.Utf8,
    "order": pl.Utf8,
    "family": pl.Utf8,
    "genus": pl.Utf8,
    "species": pl.Utf8,
    "country": pl.Utf8,
    "stateProvince": pl.Utf8,
    "decimalLatitude": pl.Float64,
    "decimalLongitude": pl.Float64,
    "coordinateUncertaintyInMeters": pl.Float64,
    "spatiallyValid": pl.Boolean,
    "geospatialKosher": pl.Utf8,
    "eventDate": pl.Int64,
    "eventDate_iso": pl.Utf8,
    "year": pl.Int64,
    "month": pl.Utf8,
    "day": pl.Utf8,
    "basisOfRecord": pl.Utf8,
    "raw_basisOfRecord": pl.Utf8,
    "dataProviderUid": pl.Utf8,
    "dataProviderName": pl.Utf8,
    "dataResourceUid": pl.Utf8,
    "dataResourceName": pl.Utf8,
    "recordedBy": pl.Utf8,
    "collectors": pl.Utf8,
    "collector": pl.Utf8,
    "license": pl.Utf8,
    "image": pl.Utf8,
    "images": pl.Utf8,
    "imageUrl": pl.Utf8,
    "largeImageUrl": pl.Utf8,
    "smallImageUrl": pl.Utf8,
    "thumbnailUrl": pl.Utf8,
    "imageUrls": pl.Utf8,
    "references": pl.Utf8,
    "occurrenceDetails": pl.Utf8,
    "identificationVerificationStatus": pl.Utf8,
    "recordNumber": pl.Utf8,
    "assertions": pl.Utf8,
    "speciesGroups": pl.Utf8,
    "latLong": pl.Utf8,
}

SCHEMA: dict[str, pl.DataType] = {field: FIELD_SCHEMA[field] for field in FIELDS}
