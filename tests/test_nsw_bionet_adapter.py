from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from sources import nsw_bionet


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, Any], timeout: int) -> FakeResponse:
        self.requests.append({"url": url, "params": params, "timeout": timeout})
        skip = int(params.get("$skip", 0))
        top = int(params.get("$top", 0))
        page_index = skip // top if top else 0
        page = self.pages[page_index] if page_index < len(self.pages) else []
        return FakeResponse({"value": page})


def bionet_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "basisOfRecord": "HumanObservation",
        "catalogNumber": "SADBI0019807",
        "occurrenceID": (
            "urn:catalog:NSW Dept of Planning, Industry and Environment:"
            "BioNet Atlas of NSW Wildlife:SADBI0019807"
        ),
        "scientificName": "Toxidia peron",
        "vernacularName": "Large Dingy Skipper",
        "taxonRank": "Species",
        "kingdom": "Animalia",
        "class": "Insecta",
        "order": "Lepidoptera",
        "family": "Hesperiidae",
        "genus": "Toxidia",
        "specificEpithet": "peron",
        "stateProvince": "NSW",
        "country": "Australia",
        "countryCode": "AU",
        "decimalLatitude": -28.395207228,
        "decimalLongitude": 153.274644408,
        "coordinateUncertaintyInMeters": 100.0,
        "coordinatePrecision": "9",
        "eventDate": "2008-10-01",
        "datasetName": "DPIE Data from Scientific Licences dataset",
        "datasetID": 1155,
        "dcterms_rights": "CC-BY 4.0",
        "dcterms_rightsHolder": "NSW National Parks and Wildlife Service",
        "dcterms_modified": "2020-04-22T10:26:07.863+10:00",
        "dataGeneralizations": "The observer name has been changed to a unique User ID",
        "informationWithheld": (
            "The following fields have been withheld and are only available to "
            "licensed or OEH staff: locality, locationRemarks, occurrenceRemarks"
        ),
        "sensitivityClass": "Not Sensitive",
        "stateConservation": "Not Listed",
        "countryConservation": "Not Listed",
        "status": "Valid and accepted without modification",
    }
    record.update(overrides)
    return record


def test_butterfly_filter_scopes_public_bionet_to_six_families() -> None:
    filter_text = nsw_bionet.build_bionet_filter(nsw_bionet.BUTTERFLY_FAMILIES)

    assert "class eq 'Insecta'" in filter_text
    assert "order eq 'Lepidoptera'" in filter_text
    for family in nsw_bionet.BUTTERFLY_FAMILIES:
        assert f"family eq '{family}'" in filter_text


def test_normalise_bionet_record_preserves_provenance_and_generalisation() -> None:
    row = nsw_bionet.normalise_bionet_record(
        bionet_record(
            dataGeneralizations="Coordinates generalised to 1 km",
            sensitivityClass="Sensitive",
        )
    )

    assert row["source"] == "nsw_bionet"
    assert row["source_jurisdiction"] == "NSW"
    assert row["source_record_id"] == "SADBI0019807"
    assert row["occurrence_id"].endswith(":SADBI0019807")
    assert row["scientific_name"] == "Toxidia peron"
    assert row["family"] == "Hesperiidae"
    assert row["event_date_start"] == "2008-10-01"
    assert row["event_date_end"] is None
    assert row["year"] == 2008
    assert row["decimal_latitude"] == -28.395207228
    assert row["coordinate_uncertainty_m"] == 100.0
    assert row["sensitive_record"] is True
    assert row["spatially_generalised"] is True
    assert row["raw_record_hash"]
    assert row["canonical_record_hash"]


def test_fetch_occurrences_pages_and_writes_canonical_parquet(tmp_path: Path) -> None:
    session = FakeSession(
        [
            [bionet_record(catalogNumber="one"), bionet_record(catalogNumber="two")],
            [bionet_record(catalogNumber="three")],
        ]
    )
    adapter = nsw_bionet.NSWBioNetAdapter(session=session)
    output = tmp_path / "nsw_bionet.parquet"

    result = adapter.fetch_occurrences(
        output_path=output,
        families=nsw_bionet.BUTTERFLY_FAMILIES,
        page_size=2,
        max_records=3,
    )

    df = pl.read_parquet(output)
    assert result.row_count == 3
    assert result.output_path == output
    assert df.height == 3
    assert set(df["source_record_id"].to_list()) == {"one", "two", "three"}
    assert all(request["params"]["$top"] == 2 for request in session.requests)
    assert session.requests[0]["params"]["$skip"] == 0
    assert session.requests[1]["params"]["$skip"] == 2
