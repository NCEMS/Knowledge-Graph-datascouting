"""Mocked unit tests for pride_client.py's bulk-listing methods."""

import responses

from pride_client import PrideClient

BASE = "https://www.ebi.ac.uk/pride/ws/archive/v3"


@responses.activate
def test_fetch_projects_page():
    responses.add(
        responses.GET, f"{BASE}/search/projects",
        json=[{"accession": "PXD1", "references": []}],
        status=200,
    )
    client = PrideClient()
    page = client.fetch_projects_page(page=0, page_size=100)
    assert page[0]["accession"] == "PXD1"


@responses.activate
def test_iter_all_projects_paginates_until_empty():
    responses.add(responses.GET, f"{BASE}/search/projects", json=[{"accession": "PXD1"}], status=200)
    responses.add(responses.GET, f"{BASE}/search/projects", json=[{"accession": "PXD2"}], status=200)
    responses.add(responses.GET, f"{BASE}/search/projects", json=[], status=200)

    client = PrideClient()
    accessions = [p["accession"] for p in client.iter_all_projects(page_size=1)]

    assert accessions == ["PXD1", "PXD2"]


def test_extract_publication_info_from_search_summary():
    client = PrideClient()
    summary = {"references": [
        "Some Title. Adv Sci (Weinh). 2026 13(51):e76205--pubMed:42348392--doi: 10.1002/advs.76205"
    ]}

    doi, pmids = client.extract_publication_info_from_search_summary(summary)

    assert doi == "10.1002/advs.76205"
    assert pmids == ["42348392"]


def test_extract_publication_info_from_search_summary_treats_pubmed_zero_as_absent():
    client = PrideClient()
    summary = {"references": ["null--pubMed:0--doi: 10.1073/PNAS.2626934123"]}

    doi, pmids = client.extract_publication_info_from_search_summary(summary)

    assert doi == "10.1073/PNAS.2626934123"
    assert pmids == []


def test_extract_publication_info_from_search_summary_no_references():
    client = PrideClient()
    assert client.extract_publication_info_from_search_summary({"references": []}) == (None, [])
    assert client.extract_publication_info_from_search_summary({}) == (None, [])
