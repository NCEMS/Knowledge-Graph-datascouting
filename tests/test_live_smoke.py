"""
Live smoke tests: hit the real upstream APIs to catch upstream breaking
changes. Skipped by default; run explicitly with:

    pytest tests/test_live_smoke.py --run-live
"""

import pytest

from metabolights_client import MetaboLightsClient
from metabolomics_workbench_client import MetabolomicsWorkbenchClient
from gnps_client import GNPSClient
from metabolomics_hub_client import MetabolomicsHubClient
from europepmc_client import EuropePMCClient
from pride_client import PrideClient
from dataset_linker import DatasetLinker

pytestmark = pytest.mark.live


def test_metabolights_live_mtbls1():
    client = MetaboLightsClient()
    details = client.fetch_study_details("MTBLS1")
    assert details["isaInvestigation"]["identifier"] == "MTBLS1"

    files = client.fetch_study_files("MTBLS1")
    assert len(files["study"]) > 0

    doi, pmids = client.extract_publication_info(details)
    assert doi or pmids


def test_metabolomics_workbench_live_st000001():
    client = MetabolomicsWorkbenchClient()
    summary = client.fetch_study_summary("ST000001")
    assert summary["study_id"] == "ST000001"

    factors = client.fetch_study_factors("ST000001")
    assert len(factors) > 0

    compound = client.fetch_compound("pubchem_cid", "311")
    assert compound["name"]


def test_gnps_live_dataset_search():
    client = GNPSClient()
    datasets = client.fetch_datasets(query="coral")
    assert len(datasets) > 0

    files = client.fetch_dataset_files(datasets[0]["accession"])
    assert isinstance(files, list)


def test_metabolomics_hub_live_portal():
    client = MetabolomicsHubClient()
    html = client.fetch_portal_page()
    assert html is not None


def test_europepmc_live_accession_mining():
    client = EuropePMCClient()
    accs = client.find_accessions_in_publication("PMC13625717")
    assert accs["pride"] == ["PXD076809"]
    assert accs["metabolights"] == ["MTBLS14236"]


def test_dataset_linker_live_bidirectional_resolution():
    """
    Confirms the PXD076809 <-> MTBLS14236 cross-omics link resolves
    identically whether starting from the proteomics side (which
    self-reports its publication) or the metabolomics side (which does not,
    and must fall back to Europe PMC text-mining).
    """
    linker = DatasetLinker()

    pride = PrideClient()
    details = pride.fetch_project_details("PXD076809")
    doi, pmids = pride.extract_publication_info(details)
    pride_link = linker.link("PXD076809", "pride", self_reported_doi=doi, self_reported_pmids=pmids)
    assert pride_link.publication.source == "self_reported"
    assert pride_link.companion_accessions.get("metabolights") == ["MTBLS14236"]

    mtbls = MetaboLightsClient()
    mdetails = mtbls.fetch_study_details("MTBLS14236")
    mdoi, mpmids = mtbls.extract_publication_info(mdetails)
    assert not mdoi and not mpmids  # MetaboLights does not self-report this publication

    mtbls_link = linker.link("MTBLS14236", "metabolights", self_reported_doi=mdoi, self_reported_pmids=mpmids)
    assert mtbls_link.publication.source == "text_mined"
    assert mtbls_link.companion_accessions.get("pride") == ["PXD076809"]
    assert mtbls_link.publication.pmid == pride_link.publication.pmid
