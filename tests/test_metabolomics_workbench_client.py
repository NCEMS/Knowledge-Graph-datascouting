"""Mocked unit tests for MetabolomicsWorkbenchClient."""

import responses

from metabolomics_workbench_client import MetabolomicsWorkbenchClient, _normalize_rows

BASE = "https://www.metabolomicsworkbench.org/rest"


def test_normalize_rows_edge_cases():
    assert _normalize_rows([]) == []
    assert _normalize_rows(None) == []
    assert _normalize_rows({"a": 1}) == [{"a": 1}]
    assert _normalize_rows({"1": {"x": 1}, "2": {"x": 2}}) == [{"x": 1}, {"x": 2}]


@responses.activate
def test_fetch_study_summary_single_result():
    responses.add(
        responses.GET, f"{BASE}/study/study_id/ST000001/summary",
        json={"study_id": "ST000001", "study_title": "Test", "license": "CC BY 4.0"},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    summary = client.fetch_study_summary("ST000001")
    assert summary["study_title"] == "Test"
    assert summary["license"] == "CC BY 4.0"


@responses.activate
def test_fetch_study_summary_not_found_returns_none():
    responses.add(responses.GET, f"{BASE}/study/study_id/ST999999/summary", json=[], status=200)
    client = MetabolomicsWorkbenchClient()
    assert client.fetch_study_summary("ST999999") is None


@responses.activate
def test_fetch_study_factors_multi_row():
    responses.add(
        responses.GET, f"{BASE}/study/study_id/ST000001/factors",
        json={"1": {"local_sample_id": "A"}, "2": {"local_sample_id": "B"}},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    factors = client.fetch_study_factors("ST000001")
    assert len(factors) == 2
    assert factors[0]["local_sample_id"] == "A"


@responses.activate
def test_fetch_study_metabolites():
    responses.add(
        responses.GET, f"{BASE}/study/study_id/ST000001/metabolites",
        json={"1": {"metabolite_name": "glucose", "refmet_name": "Glucose"}},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    metabolites = client.fetch_study_metabolites("ST000001")
    assert metabolites[0]["refmet_name"] == "Glucose"


@responses.activate
def test_fetch_study_analysis():
    responses.add(
        responses.GET, f"{BASE}/study/study_id/ST000001/analysis",
        json={"study_id": "ST000001", "analysis_type": "MS"},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    analysis = client.fetch_study_analysis("ST000001")
    assert analysis[0]["analysis_type"] == "MS"


@responses.activate
def test_fetch_study_data():
    responses.add(
        responses.GET, f"{BASE}/study/study_id/ST000001/data",
        json={"1": {"metabolite_name": "glucose", "DATA": {"s1": "100"}}},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    data = client.fetch_study_data("ST000001")
    assert data[0]["DATA"]["s1"] == "100"


@responses.activate
def test_fetch_compound():
    responses.add(
        responses.GET, f"{BASE}/compound/pubchem_cid/311/all",
        json={"pubchem_cid": "311", "name": "Citric acid", "inchi_key": "KRKNYBCHXYNGOX-UHFFFAOYSA-N"},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    compound = client.fetch_compound("pubchem_cid", "311")
    assert compound["name"] == "Citric acid"


@responses.activate
def test_fetch_compound_not_found_returns_none():
    responses.add(responses.GET, f"{BASE}/compound/pubchem_cid/0/all", json=[], status=200)
    client = MetabolomicsWorkbenchClient()
    assert client.fetch_compound("pubchem_cid", "0") is None


@responses.activate
def test_resolve_refmet_exact():
    responses.add(
        responses.GET, f"{BASE}/refmet/name/Cholesterol/all",
        json={"refmet_id": "RM0135639", "super_class": "Sterol Lipids"},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    refmet = client.resolve_refmet("Cholesterol")
    assert refmet["refmet_id"] == "RM0135639"


@responses.activate
def test_resolve_refmet_fuzzy_match_uses_match_endpoint():
    responses.add(
        responses.GET, f"{BASE}/refmet/match/LysoPC16:0/all",
        json={"refmet_id": "RM0999999"},
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    refmet = client.resolve_refmet("LysoPC16:0", exact=False)
    assert refmet["refmet_id"] == "RM0999999"


@responses.activate
def test_fetch_all_study_ids_dedupes_and_sorts():
    responses.add(
        responses.GET, f"{BASE}/study/study_id/ST/available",
        json={
            "1": {"project_id": "PR000001", "study_id": "ST000001", "analysis_id": "AN000001"},
            "2": {"project_id": "PR000001", "study_id": "ST000001", "analysis_id": "AN000002"},
            "3": {"project_id": "PR000002", "study_id": "ST000002", "analysis_id": "AN000003"},
        },
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    study_ids = client.fetch_all_study_ids()
    assert study_ids == ["ST000001", "ST000002"]


@responses.activate
def test_fetch_study_mwtab_text():
    responses.add(
        responses.GET, f"{BASE}/study/study_id/ST000001/mwtab/txt",
        body="PR:PROJECT_TITLE\tTest\n",
        status=200,
    )
    client = MetabolomicsWorkbenchClient()
    text = client.fetch_study_mwtab_text("ST000001")
    assert "PR:PROJECT_TITLE" in text


def test_extract_publication_info_from_mwtab_pubmed_only_no_real_doi():
    client = MetabolomicsWorkbenchClient()
    text = (
        "PR:PUBLICATIONS\tQuality control for plant metabolomics. DOI:\n"
        "PR:PUBLICATIONS\thttp://www.ncbi.nlm.nih.gov/pubmed/18269577 (PubMed)\n"
    )
    doi, pmid = client.extract_publication_info_from_mwtab(text)
    # The empty "DOI:" label must not be mistaken for the adjacent PubMed URL
    assert doi is None
    assert pmid == "18269577"


def test_extract_publication_info_from_mwtab_wrapped_lines_with_real_doi():
    client = MetabolomicsWorkbenchClient()
    text = (
        "PR:PUBLICATIONS\tSkogerson K1 et al. J Agric Food Chem. 2009;57(15):6899-907. doi:\n"
        "PR:PUBLICATIONS\t10.1021/jf9019322. Comparison of GC-TOF and NMR.\n"
        "PR:PUBLICATIONS\t<a href=http://www.ncbi.nlm.nih.gov/pubmed/19588931>PubMed</a>\n"
    )
    doi, pmid = client.extract_publication_info_from_mwtab(text)
    assert doi == "10.1021/jf9019322"
    assert pmid == "19588931"


def test_extract_publication_info_from_mwtab_no_publications_field():
    client = MetabolomicsWorkbenchClient()
    doi, pmid = client.extract_publication_info_from_mwtab("PR:PROJECT_TITLE\tTest\n")
    assert doi is None
    assert pmid is None
