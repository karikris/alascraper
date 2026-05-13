from __future__ import annotations

import alascraper as a


def test_build_keyword_params_includes_query_fields_and_filters() -> None:
    params = a.build_keyword_params(
        query="butterfly",
        start=500,
        page_size=250,
        facet_filters=('country:"Australia"', 'order:"Lepidoptera"'),
    )

    assert ("q", "butterfly") in params
    assert ("start", 500) in params
    assert ("pageSize", 250) in params
    assert ("facet", "false") in params
    assert ("fq", 'country:"Australia"') in params
    assert ("fq", 'order:"Lepidoptera"') in params
    assert ("fl", ",".join(a.KEYWORD_DISCOVERY_FIELDS)) in params


def test_keyword_records_to_discoveries_extracts_scientific_names() -> None:
    discoveries = a.keyword_records_to_discoveries(
        "general_butterfly",
        "butterfly",
        [
            {
                "scientificName": "Papilio aegeus",
                "taxonConceptID": "urn:lsid:papilio-aegeus",
                "vernacularName": "Orchard Swallowtail",
                "taxonRank": "species",
                "family": "Papilionidae",
                "order": "Lepidoptera",
            },
            {"scientificName": "Lepidoptera"},
        ],
    )

    assert discoveries == [
        a.KeywordSpeciesDiscovery(
            keyword_key="general_butterfly",
            query="butterfly",
            scientific_name="Papilio aegeus",
            taxon_lsid="urn:lsid:papilio-aegeus",
            common_name="Orchard Swallowtail",
            taxon_rank="species",
            family="Papilionidae",
            order="Lepidoptera",
        )
    ]


def test_placeholder_taxon_labels_are_not_scientific_names() -> None:
    assert not a.is_probable_scientific_name("Not supplied")
    assert not a.is_probable_scientific_name("Unknown species")
    assert not a.is_probable_scientific_name("Unidentified taxon")


def test_discoveries_to_species_targets_dedupes_by_lsid() -> None:
    discoveries = [
        a.KeywordSpeciesDiscovery(
            keyword_key="general_butterfly",
            query="butterfly",
            scientific_name="Papilio aegeus",
            taxon_lsid="urn:lsid:same",
            common_name="Orchard Swallowtail",
            taxon_rank="species",
            family="Papilionidae",
            order="Lepidoptera",
        ),
        a.KeywordSpeciesDiscovery(
            keyword_key="swallowtail",
            query="swallowtail",
            scientific_name="Papilio aegeus",
            taxon_lsid="urn:lsid:same",
            common_name=None,
            taxon_rank="species",
            family="Papilionidae",
            order="Lepidoptera",
        ),
    ]

    targets = a.discoveries_to_species_targets(discoveries)

    assert targets == [
        a.SpeciesTarget(
            key="papilio_aegeus",
            scientific_name="Papilio aegeus",
            common_name="Orchard Swallowtail",
            taxon_lsid="urn:lsid:same",
        )
    ]
