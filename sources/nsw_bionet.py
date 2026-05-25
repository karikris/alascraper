from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from constants import TIMEOUT_SECONDS, USER_AGENT
from schemas.occurrence import (
    canonical_hash,
    clean_text,
    hash_payload,
    parse_event_date,
    to_float,
    utc_timestamp,
    write_occurrence_parquet,
)
from sources.base import SourceFetchResult


BIONET_ODATA_BASE_URL = "https://data.bionet.nsw.gov.au/biosvcapp/odata"
BIONET_SPECIES_SIGHTINGS_ENTITY = "SpeciesSightings_CoreData"
SOURCE = "nsw_bionet"
JURISDICTION = "NSW"

BUTTERFLY_FAMILIES = (
    "Hesperiidae",
    "Papilionidae",
    "Pieridae",
    "Nymphalidae",
    "Riodinidae",
    "Lycaenidae",
)

PUBLIC_SELECTED_FIELDS = (
    "basisOfRecord",
    "catalogNumber",
    "occurrenceID",
    "scientificName",
    "vernacularName",
    "taxonRank",
    "kingdom",
    "class",
    "order",
    "family",
    "genus",
    "specificEpithet",
    "infraspecificEpithet",
    "stateProvince",
    "country",
    "countryCode",
    "decimalLatitude",
    "decimalLongitude",
    "coordinateUncertaintyInMeters",
    "coordinatePrecision",
    "geodeticDatum",
    "eventDate",
    "datasetName",
    "datasetID",
    "dcterms_rights",
    "dcterms_rightsHolder",
    "dcterms_modified",
    "dcterms_available",
    "dataGeneralizations",
    "informationWithheld",
    "sensitivityClass",
    "stateConservation",
    "countryConservation",
    "status",
    "occurrenceStatus",
    "individualCount",
    "observationType",
    "establishmentMeans",
)

DEFAULT_PAGE_SIZE = 1_000


def quote_odata_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_bionet_filter(
    families: tuple[str, ...] = BUTTERFLY_FAMILIES,
    *,
    state_province: str = "NSW",
) -> str:
    family_filter = " or ".join(
        f"family eq {quote_odata_string(family)}" for family in families
    )
    return " and ".join(
        [
            "class eq 'Insecta'",
            "order eq 'Lepidoptera'",
            f"stateProvince eq {quote_odata_string(state_province)}",
            f"({family_filter})",
        ]
    )


def is_sensitive_record(record: dict[str, Any]) -> bool:
    sensitivity_class = clean_text(record.get("sensitivityClass"))
    return bool(sensitivity_class and sensitivity_class.lower() != "not sensitive")


def is_spatially_generalised(record: dict[str, Any]) -> bool:
    if is_sensitive_record(record):
        return True

    data_generalizations = (clean_text(record.get("dataGeneralizations")) or "").lower()
    spatial_terms = ("coordinate", "spatial", "location", "generalised", "generalized")
    return any(term in data_generalizations for term in spatial_terms)


def normalise_bionet_record(record: dict[str, Any]) -> dict[str, Any]:
    event_date = clean_text(record.get("eventDate"))
    event_date_start, event_date_end, year = parse_event_date(event_date)
    raw_payload = {
        field: record.get(field)
        for field in PUBLIC_SELECTED_FIELDS
        if field in record
    }

    row: dict[str, Any] = {
        "source": SOURCE,
        "source_jurisdiction": JURISDICTION,
        "source_dataset": clean_text(record.get("datasetName")),
        "source_record_id": clean_text(record.get("catalogNumber")),
        "source_record_url": None,
        "source_updated_at": clean_text(record.get("dcterms_modified")),
        "ingested_at_utc": utc_timestamp(),
        "occurrence_id": clean_text(record.get("occurrenceID")),
        "catalog_number": clean_text(record.get("catalogNumber")),
        "scientific_name": clean_text(record.get("scientificName")),
        "vernacular_name": clean_text(record.get("vernacularName")),
        "taxon_rank": clean_text(record.get("taxonRank")),
        "kingdom": clean_text(record.get("kingdom")),
        "class_name": clean_text(record.get("class")),
        "order": clean_text(record.get("order")),
        "family": clean_text(record.get("family")),
        "genus": clean_text(record.get("genus")),
        "specific_epithet": clean_text(record.get("specificEpithet")),
        "infraspecific_epithet": clean_text(record.get("infraspecificEpithet")),
        "country": clean_text(record.get("country")),
        "country_code": clean_text(record.get("countryCode")),
        "state_province": clean_text(record.get("stateProvince")),
        "decimal_latitude": to_float(record.get("decimalLatitude")),
        "decimal_longitude": to_float(record.get("decimalLongitude")),
        "coordinate_uncertainty_m": to_float(record.get("coordinateUncertaintyInMeters")),
        "coordinate_precision": clean_text(record.get("coordinatePrecision")),
        "geodetic_datum": clean_text(record.get("geodeticDatum")),
        "event_date": event_date,
        "event_date_start": event_date_start,
        "event_date_end": event_date_end,
        "year": year,
        "basis_of_record": clean_text(record.get("basisOfRecord")),
        "occurrence_status": clean_text(record.get("occurrenceStatus")),
        "record_status": clean_text(record.get("status")),
        "individual_count": to_float(record.get("individualCount")),
        "observation_type": clean_text(record.get("observationType")),
        "establishment_means": clean_text(record.get("establishmentMeans")),
        "state_conservation": clean_text(record.get("stateConservation")),
        "country_conservation": clean_text(record.get("countryConservation")),
        "sensitive_record": is_sensitive_record(record),
        "spatially_generalised": is_spatially_generalised(record),
        "data_generalizations": clean_text(record.get("dataGeneralizations")),
        "information_withheld": clean_text(record.get("informationWithheld")),
        "license": clean_text(record.get("dcterms_rights")),
        "rights_holder": clean_text(record.get("dcterms_rightsHolder")),
        "raw_source_payload": json.dumps(
            raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "raw_record_hash": hash_payload(raw_payload),
    }
    row["canonical_record_hash"] = canonical_hash(row)
    return row


class NSWBioNetAdapter:
    source = SOURCE
    jurisdiction = JURISDICTION

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        base_url: str = BIONET_ODATA_BASE_URL,
        timeout_seconds: int = TIMEOUT_SECONDS,
    ) -> None:
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        if isinstance(self.session, requests.Session):
            self.session.headers.update({"User-Agent": USER_AGENT})

    @property
    def occurrences_url(self) -> str:
        return f"{self.base_url}/{BIONET_SPECIES_SIGHTINGS_ENTITY}"

    def fetch_occurrences(
        self,
        *,
        output_path: Path,
        families: tuple[str, ...] = BUTTERFLY_FAMILIES,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_records: int | None = None,
        metadata_path: Path | None = None,
    ) -> SourceFetchResult:
        if page_size <= 0:
            raise ValueError("page_size must be positive.")
        if max_records is not None and max_records < 0:
            raise ValueError("max_records must be positive or None.")

        rows: list[dict[str, Any]] = []
        skip = 0
        filter_text = build_bionet_filter(families)

        while max_records is None or len(rows) < max_records:
            params = {
                "$top": page_size,
                "$skip": skip,
                "$filter": filter_text,
                "$orderby": "catalogNumber asc",
                "$select": ",".join(PUBLIC_SELECTED_FIELDS),
            }
            response = self.session.get(
                self.occurrences_url,
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            records = response.json().get("value", [])
            if not records:
                break

            remaining = None if max_records is None else max_records - len(rows)
            selected_records = records if remaining is None else records[:remaining]
            rows.extend(normalise_bionet_record(record) for record in selected_records)

            if len(records) < page_size:
                break
            skip += page_size

        write_occurrence_parquet(rows, output_path)
        resolved_metadata_path = metadata_path or output_path.with_name("metadata.json")
        self.write_metadata(
            path=resolved_metadata_path,
            output_path=output_path,
            row_count=len(rows),
            families=families,
            page_size=page_size,
            max_records=max_records,
            filter_text=filter_text,
        )
        return SourceFetchResult(
            source=self.source,
            jurisdiction=self.jurisdiction,
            output_path=output_path,
            metadata_path=resolved_metadata_path,
            row_count=len(rows),
        )

    def write_metadata(
        self,
        *,
        path: Path,
        output_path: Path,
        row_count: int,
        families: tuple[str, ...],
        page_size: int,
        max_records: int | None,
        filter_text: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": self.source,
            "jurisdiction": self.jurisdiction,
            "entity": BIONET_SPECIES_SIGHTINGS_ENTITY,
            "base_url": self.base_url,
            "output_path": str(output_path),
            "row_count": row_count,
            "families": list(families),
            "page_size": page_size,
            "max_records": max_records,
            "filter": filter_text,
            "selected_fields": list(PUBLIC_SELECTED_FIELDS),
            "built_at_utc": utc_timestamp(),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
