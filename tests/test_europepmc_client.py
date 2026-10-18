"""Mocked unit tests for EuropePMCClient."""

import responses

from europepmc_client import EuropePMCClient, ACCESSION_PATTERNS

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


@responses.activate
def test_search():
    responses.add(
        responses.GET, f"{BASE}/search",
        json={"hitCount": 1, "resultList": {"result": [{"pmid": "123", "title": "x"}]}},
        status=200,
    )
    client = EuropePMCClient()
    results = client.search("test query")
    assert len(results) == 1
    assert results[0]["pmid"] == "123"


@responses.activate
def test_search_no_results():
    responses.add(
        responses.GET, f"{BASE}/search",
        json={"hitCount": 0, "resultList": {"result": []}},
        status=200,
    )
    client = EuropePMCClient()
    assert client.search("nonexistent") == []


@responses.activate
def test_fetch_publication_by_pmid():
    responses.add(
        responses.GET, f"{BASE}/search",
        json={"hitCount": 1, "resultList": {"result": [
            {"pmid": "42802602", "pmcid": "PMC13625717", "doi": "10.1080/21505594.2026.2739013"}
        ]}},
        status=200,
    )
    client = EuropePMCClient()
    pub = client.fetch_publication(pmid="42802602")
    assert pub["pmcid"] == "PMC13625717"


def test_fetch_publication_requires_an_identifier():
    client = EuropePMCClient()
    assert client.fetch_publication() is None


@responses.activate
def test_find_publications_mentioning_accession():
    responses.add(
        responses.GET, f"{BASE}/search",
        json={"hitCount": 1, "resultList": {"result": [{"pmid": "42802602", "pmcid": "PMC13625717"}]}},
        status=200,
    )
    client = EuropePMCClient()
    hits = client.find_publications_mentioning_accession("MTBLS14236")
    assert hits[0]["pmid"] == "42802602"


@responses.activate
def test_find_accessions_in_publication():
    responses.add(
        responses.GET, f"{BASE}/PMC13625717/fullTextXML",
        body="<article>See PXD076809 and MTBLS14236 and PRJNA1447169 for data.</article>",
        status=200,
    )
    client = EuropePMCClient()
    accs = client.find_accessions_in_publication("PMC13625717")
    assert accs["pride"] == ["PXD076809"]
    assert accs["metabolights"] == ["MTBLS14236"]
    assert accs["bioproject"] == ["PRJNA1447169"]
    assert "massive" not in accs


@responses.activate
def test_find_accessions_in_publication_no_full_text_returns_empty():
    responses.add(responses.GET, f"{BASE}/PMCXXXXXXX/fullTextXML", status=404)
    client = EuropePMCClient()
    assert client.find_accessions_in_publication("PMCXXXXXXX") == {}


def test_accession_patterns_do_not_cross_match():
    text = "PXD076809 MTBLS14236 ST000001 MSV000086989 PRJNA1447169 SAMN12345678"
    import re
    assert re.findall(ACCESSION_PATTERNS["pride"], text) == ["PXD076809"]
    assert re.findall(ACCESSION_PATTERNS["metabolights"], text) == ["MTBLS14236"]
    assert re.findall(ACCESSION_PATTERNS["metabolomics_workbench"], text) == ["ST000001"]
    assert re.findall(ACCESSION_PATTERNS["massive"], text) == ["MSV000086989"]
    assert re.findall(ACCESSION_PATTERNS["bioproject"], text) == ["PRJNA1447169"]
    assert re.findall(ACCESSION_PATTERNS["biosample"], text) == ["SAMN12345678"]
