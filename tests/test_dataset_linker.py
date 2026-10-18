"""Mocked unit tests for DatasetLinker / PublicationIdentity / DatasetLink."""

from unittest.mock import MagicMock

from dataset_linker import DatasetLinker, PublicationIdentity, DatasetLink
from europepmc_client import EuropePMCClient


def test_publication_identity_is_known():
    assert PublicationIdentity().is_known() is False
    assert PublicationIdentity(pmid="123").is_known() is True
    assert PublicationIdentity(doi="10.1/x").is_known() is True


def test_publication_identity_to_dict():
    pub = PublicationIdentity(pmid="1", pmcid="PMC1", doi="10.1/x", source="self_reported")
    assert pub.to_dict() == {"pmid": "1", "pmcid": "PMC1", "doi": "10.1/x", "source": "self_reported"}


def test_dataset_link_to_dict():
    link = DatasetLink("PXD1", "pride", PublicationIdentity(pmid="1", source="self_reported"),
                        {"metabolights": ["MTBLS1"]})
    d = link.to_dict()
    assert d["accession"] == "PXD1"
    assert d["companion_accessions"] == {"metabolights": ["MTBLS1"]}


def test_resolve_publication_prefers_self_reported():
    mock_epmc = MagicMock(spec=EuropePMCClient)
    linker = DatasetLinker(europepmc_client=mock_epmc)

    pub = linker.resolve_publication("PXD1", "pride", self_reported_doi="10.1/x", self_reported_pmids=["111"])

    assert pub.source == "self_reported"
    assert pub.pmid == "111"
    assert pub.doi == "10.1/x"
    mock_epmc.find_publications_mentioning_accession.assert_not_called()


def test_resolve_publication_falls_back_to_text_mining():
    mock_epmc = MagicMock(spec=EuropePMCClient)
    mock_epmc.find_publications_mentioning_accession.return_value = [
        {"pmid": "42802602", "pmcid": "PMC13625717", "doi": "10.1080/21505594.2026.2739013"}
    ]
    linker = DatasetLinker(europepmc_client=mock_epmc)

    pub = linker.resolve_publication("MTBLS14236", "metabolights", self_reported_doi=None, self_reported_pmids=[])

    assert pub.source == "text_mined"
    assert pub.pmid == "42802602"
    assert pub.pmcid == "PMC13625717"
    mock_epmc.find_publications_mentioning_accession.assert_called_once_with("MTBLS14236")


def test_resolve_publication_unresolved_when_nothing_found():
    mock_epmc = MagicMock(spec=EuropePMCClient)
    mock_epmc.find_publications_mentioning_accession.return_value = []
    linker = DatasetLinker(europepmc_client=mock_epmc)

    pub = linker.resolve_publication("PXD999", "pride", self_reported_doi=None, self_reported_pmids=[])

    assert pub.source == "unresolved"
    assert pub.is_known() is False


def test_find_companion_datasets_resolves_pmcid_from_pmid_when_missing():
    mock_epmc = MagicMock(spec=EuropePMCClient)
    mock_epmc.fetch_publication.return_value = {"pmcid": "PMC13625717"}
    mock_epmc.find_accessions_in_publication.return_value = {"metabolights": ["MTBLS14236"]}
    linker = DatasetLinker(europepmc_client=mock_epmc)

    pub = PublicationIdentity(pmid="42802602", source="self_reported")
    companions = linker.find_companion_datasets(pub)

    assert companions == {"metabolights": ["MTBLS14236"]}
    assert pub.pmcid == "PMC13625717"
    mock_epmc.fetch_publication.assert_called_once_with(pmid="42802602")


def test_find_companion_datasets_returns_empty_without_pmcid():
    mock_epmc = MagicMock(spec=EuropePMCClient)
    mock_epmc.fetch_publication.return_value = None
    linker = DatasetLinker(europepmc_client=mock_epmc)

    pub = PublicationIdentity(pmid="42802602")
    assert linker.find_companion_datasets(pub) == {}


def test_link_excludes_self_from_companion_accessions():
    mock_epmc = MagicMock(spec=EuropePMCClient)
    mock_epmc.find_accessions_in_publication.return_value = {
        "pride": ["PXD076809"],
        "metabolights": ["MTBLS14236"],
    }
    linker = DatasetLinker(europepmc_client=mock_epmc)

    link = linker.link("PXD076809", "pride", self_reported_doi="10.1/x", self_reported_pmids=["1"])

    # pride's own accession should be excluded from its own companion list
    assert "pride" not in link.companion_accessions
    assert link.companion_accessions == {"metabolights": ["MTBLS14236"]}


def test_link_skips_companion_lookup_when_publication_unresolved():
    mock_epmc = MagicMock(spec=EuropePMCClient)
    mock_epmc.find_publications_mentioning_accession.return_value = []
    linker = DatasetLinker(europepmc_client=mock_epmc)

    link = linker.link("PXD999", "pride", self_reported_doi=None, self_reported_pmids=[])

    assert link.publication.source == "unresolved"
    assert link.companion_accessions == {}
    mock_epmc.find_accessions_in_publication.assert_not_called()
