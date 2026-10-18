"""Mocked unit tests for MetaboLightsClient."""

import responses

from metabolights_client import MetaboLightsClient

BASE = "https://www.ebi.ac.uk/metabolights/ws"


@responses.activate
def test_fetch_study_details():
    responses.add(
        responses.GET, f"{BASE}/studies/MTBLS1",
        json={"mtblsStudy": {"studyHttpUrl": "http://ftp.example.org/MTBLS1"},
              "isaInvestigation": {"identifier": "MTBLS1", "studies": []}},
        status=200,
    )
    client = MetaboLightsClient()
    details = client.fetch_study_details("MTBLS1")
    assert details["isaInvestigation"]["identifier"] == "MTBLS1"


@responses.activate
def test_fetch_study_files():
    responses.add(
        responses.GET, f"{BASE}/studies/MTBLS1/files",
        json={"study": [{"file": "s_MTBLS1.txt", "type": "metadata_sample"}],
              "latest": [], "private": []},
        status=200,
    )
    client = MetaboLightsClient()
    files = client.fetch_study_files("MTBLS1")
    assert len(files["study"]) == 1


def test_extract_file_download_links():
    client = MetaboLightsClient()
    study_details = {"mtblsStudy": {"studyFtpUrl": "ftp://x/MTBLS1", "studyHttpUrl": "http://x/MTBLS1"}}
    study_files = {"study": [{"file": "a.txt", "type": "metadata_assay"}], "latest": [], "private": []}

    result = client.extract_file_download_links("MTBLS1", study_details=study_details, study_files=study_files)

    assert result["totalCount"] == 1
    assert result["files"][0]["ftpUrl"] == "ftp://x/MTBLS1/a.txt"
    assert result["files"][0]["httpUrl"] == "http://x/MTBLS1/a.txt"
    assert result["files"][0]["type"] == "metadata_assay"


def test_extract_file_download_links_missing_data_returns_none():
    client = MetaboLightsClient()
    client.fetch_study_details = lambda mtbls_id: None
    client.fetch_study_files = lambda mtbls_id: None

    result = client.extract_file_download_links("MTBLSXXX")

    assert result is None


def test_extract_publication_info():
    client = MetaboLightsClient()
    study_details = {
        "isaInvestigation": {
            "studies": [
                {"publications": [{"pubMedID": "17190852", "doi": "10.1152/physiolgenomics.00194.2006"}]}
            ]
        }
    }

    doi, pmids = client.extract_publication_info(study_details)

    assert doi == "10.1152/physiolgenomics.00194.2006"
    assert pmids == ["17190852"]


def test_extract_publication_info_no_publications():
    client = MetaboLightsClient()
    study_details = {"isaInvestigation": {"studies": [{"publications": []}]}}

    doi, pmids = client.extract_publication_info(study_details)

    assert doi is None
    assert pmids == []


@responses.activate
def test_fetch_maf_table():
    study_details = {"mtblsStudy": {"studyHttpUrl": "http://ftp.example.org/MTBLS1"}}
    responses.add(
        responses.GET, "http://ftp.example.org/MTBLS1/m_test_maf.tsv",
        body="database_identifier\tspecies\nCHEBI:123\tHomo sapiens\n",
        status=200,
    )
    client = MetaboLightsClient()

    rows = client.fetch_maf_table("MTBLS1", "m_test_maf.tsv", study_details=study_details)

    assert len(rows) == 1
    assert rows[0]["species"] == "Homo sapiens"
    assert rows[0]["database_identifier"] == "CHEBI:123"


def test_fetch_maf_table_missing_study_details_returns_empty():
    client = MetaboLightsClient()
    client.fetch_study_details = lambda mtbls_id: None

    rows = client.fetch_maf_table("MTBLSXXX", "whatever.tsv")

    assert rows == []


@responses.activate
def test_fetch_all_study_accessions():
    responses.add(
        responses.GET, f"{BASE}/studies",
        json={"content": ["MTBLS1", "MTBLS2", "MTBLS3"], "studies": []},
        status=200,
    )
    client = MetaboLightsClient()
    accessions = client.fetch_all_study_accessions()
    assert accessions == ["MTBLS1", "MTBLS2", "MTBLS3"]


@responses.activate
def test_fetch_all_study_accessions_empty_response():
    responses.add(responses.GET, f"{BASE}/studies", json=None, status=200)
    client = MetaboLightsClient()
    assert client.fetch_all_study_accessions() == []
