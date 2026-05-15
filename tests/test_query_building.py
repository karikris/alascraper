from __future__ import annotations

import alascraper as a


def test_build_query_uses_scientific_name_without_lsid(target: a.SpeciesTarget) -> None:
    assert a.build_query(target) == "Testus species"


def test_build_query_uses_lsid_when_available(lsid_target: a.SpeciesTarget) -> None:
    assert a.build_query(lsid_target) == f"lsid:{lsid_target.taxon_lsid}"


def test_generated_target_uses_exact_facet_query() -> None:
    target = a.SpeciesTarget(
        key="poa_annua",
        scientific_name="Poa annua",
        source_order="Poales",
        facet_fq_filters=('species:"Poa annua"', 'subspecies:"Poa annua"'),
    )

    assert a.build_query(target) == "*:*"
    assert a.build_fq_filters(target) == [
        'country:"Australia"',
        'order:"Poales"',
        '(species:"Poa annua" OR subspecies:"Poa annua")',
    ]


def test_build_fq_filters_includes_country_by_default(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "COUNTRY_FILTER_ENABLED", True)
    monkeypatch.setattr(a, "COUNTRY_FILTER", 'country:"Australia"')

    assert a.build_fq_filters(target) == ['country:"Australia"']


def test_build_fq_filters_can_disable_country(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "COUNTRY_FILTER_ENABLED", False)

    assert a.build_fq_filters(target) == []


def test_no_lsid_exact_taxon_filter_is_off_by_default(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "COUNTRY_FILTER_ENABLED", False)
    monkeypatch.setattr(a, "EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID", False)

    assert a.build_fq_filters(target) == []


def test_no_lsid_exact_taxon_filter_can_be_enabled(monkeypatch, target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "COUNTRY_FILTER_ENABLED", False)
    monkeypatch.setattr(a, "EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID", True)

    assert a.build_fq_filters(target) == ['taxon_name:"Testus species"']


def test_lsid_exact_taxon_filter_is_independent(monkeypatch, lsid_target: a.SpeciesTarget) -> None:
    monkeypatch.setattr(a, "COUNTRY_FILTER_ENABLED", False)
    monkeypatch.setattr(a, "EXACT_TAXON_NAME_FILTER_WHEN_NO_LSID", True)
    monkeypatch.setattr(a, "EXACT_TAXON_NAME_FILTER_WHEN_LSID_SUPPLIED", False)

    assert a.build_fq_filters(lsid_target) == []

    monkeypatch.setattr(a, "EXACT_TAXON_NAME_FILTER_WHEN_LSID_SUPPLIED", True)
    assert a.build_fq_filters(lsid_target) == ['taxon_name:"Lsidus species"']


def test_quote_fq_escapes_double_quotes() -> None:
    assert a.quote_fq("taxon_name", 'A "quoted" taxon') == 'taxon_name:"A \\"quoted\\" taxon"'
