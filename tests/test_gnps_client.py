"""Mocked unit tests for GNPSClient."""

import responses

from gnps_client import GNPSClient, _encode_filters

EXPLORER = "https://explorer.gnps2.org/api"
FASST = "https://fasst.gnps2.org"
REDU = "https://redu.gnps2.org"
STRUCTURE = "https://structure.gnps2.org"
NPCLASSIFIER = "https://npclassifier.gnps2.org"


def test_encode_filters_default_and_custom():
    assert _encode_filters(None) == "[]"
    assert _encode_filters([{"a": 1}]) == '[{"a": 1}]'


@responses.activate
def test_fetch_datasets():
    responses.add(
        responses.GET, f"{EXPLORER}/datasets",
        json={"count": 1, "returned": 1, "truncated": False,
              "datasets": [{"accession": "MSV000001", "repository": "MassIVE"}]},
        status=200,
    )
    client = GNPSClient()
    datasets = client.fetch_datasets(query="test")
    assert len(datasets) == 1
    assert datasets[0]["accession"] == "MSV000001"


@responses.activate
def test_fetch_datasets_empty_result():
    responses.add(
        responses.GET, f"{EXPLORER}/datasets",
        json={"count": 0, "returned": 0, "truncated": False, "datasets": []},
        status=200,
    )
    client = GNPSClient()
    assert client.fetch_datasets(query="nonexistent") == []


@responses.activate
def test_fetch_dataset_files():
    responses.add(
        responses.GET, f"{EXPLORER}/datasets/MSV000001/files",
        json=[{"filename": "a.mzML", "collection": "RAW_Files"}],
        status=200,
    )
    client = GNPSClient()
    files = client.fetch_dataset_files("MSV000001")
    assert files[0]["filename"] == "a.mzML"


@responses.activate
def test_search_by_usi():
    responses.add(responses.GET, f"{FASST}/search", json={"results": []}, status=200)
    client = GNPSClient()
    result = client.search_by_usi("mzspec:GNPS:GNPS-LIBRARY:accession:CCMSLIB00000001547")
    assert result == {"results": []}


@responses.activate
def test_search_by_peaks():
    responses.add(responses.GET, f"{FASST}/search", json={"results": [{"match": True}]}, status=200)
    client = GNPSClient()
    result = client.search_by_peaks(precursor_mz=271.1, peaks=[[180.08, 100.0]], charge=1)
    assert result["results"][0]["match"] is True


@responses.activate
def test_fetch_attribute_terms():
    responses.add(
        responses.GET, f"{REDU}/attribute/SampleType/attributeterms",
        json=[{"attributename": "SampleType", "attributeterm": "feces", "countfiles": 10}],
        status=200,
    )
    client = GNPSClient()
    terms = client.fetch_attribute_terms("SampleType")
    assert terms[0]["attributeterm"] == "feces"


@responses.activate
def test_fetch_sample_metadata_url_encodes_term():
    responses.add(
        responses.GET,
        f"{REDU}/attribute/NCBITaxonomy/attributeterm/9606%7CHomo%20sapiens/files",
        json=[{"USI": "mzspec:MSV000001:x.mzML"}],
        status=200,
    )
    client = GNPSClient()
    rows = client.fetch_sample_metadata("NCBITaxonomy", "9606|Homo sapiens")
    assert rows[0]["USI"] == "mzspec:MSV000001:x.mzML"


@responses.activate
def test_convert_structure():
    responses.add(responses.GET, f"{STRUCTURE}/inchikey", body="LFQSCWFLJHTTHZ-UHFFFAOYSA-N", status=200)
    client = GNPSClient()
    key = client.convert_structure("inchikey", smiles="CCO")
    assert key == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"


def test_convert_structure_requires_input():
    client = GNPSClient()
    assert client.convert_structure("inchikey") is None


@responses.activate
def test_classify_structure_np():
    responses.add(
        responses.GET, f"{NPCLASSIFIER}/classify",
        json={"class_results": ["Purine alkaloids"], "isglycoside": False},
        status=200,
    )
    client = GNPSClient()
    result = client.classify_structure_np("Cn1c(=O)c2c(ncn2C)n(C)c1=O")
    assert result["class_results"] == ["Purine alkaloids"]
