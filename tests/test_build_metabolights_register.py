"""Unit tests for build_metabolights_register.py's orchestration logic (mocked clients)."""

from unittest.mock import MagicMock, patch

from build_metabolights_register import build_metabolights_register
from dataset_register_db import get_connection


@patch("build_metabolights_register.EuropePMCClient")
@patch("build_metabolights_register.MetaboLightsClient")
def test_self_reported_path_skips_europepmc(mock_mtbls_cls, mock_epmc_cls, tmp_path):
    mock_mtbls = MagicMock()
    mock_mtbls.fetch_all_study_accessions.return_value = ["MTBLS1"]
    mock_mtbls.fetch_study_details.return_value = {"fake": "details"}
    mock_mtbls.extract_publication_info.return_value = ("10.1/x", ["111"])
    mock_mtbls_cls.return_value = mock_mtbls

    mock_epmc = MagicMock()
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_metabolights_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute("SELECT pmid, doi, resolution_source FROM dataset_register WHERE accession='MTBLS1'").fetchone()
    assert row == ("111", "10.1/x", "self_reported")
    mock_epmc.find_publications_mentioning_accession.assert_not_called()


@patch("build_metabolights_register.EuropePMCClient")
@patch("build_metabolights_register.MetaboLightsClient")
def test_falls_back_to_europepmc_when_self_report_empty(mock_mtbls_cls, mock_epmc_cls, tmp_path):
    mock_mtbls = MagicMock()
    mock_mtbls.fetch_all_study_accessions.return_value = ["MTBLS14236"]
    mock_mtbls.fetch_study_details.return_value = {"fake": "details"}
    mock_mtbls.extract_publication_info.return_value = (None, [])
    mock_mtbls_cls.return_value = mock_mtbls

    mock_epmc = MagicMock()
    mock_epmc.find_publications_mentioning_accession.return_value = [
        {"pmid": "42802602", "pmcid": "PMC13625717", "doi": "10.1080/21505594.2026.2739013"}
    ]
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_metabolights_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT pmid, pmcid, doi, resolution_source FROM dataset_register WHERE accession='MTBLS14236'"
    ).fetchone()
    assert row == ("42802602", "PMC13625717", "10.1080/21505594.2026.2739013", "text_mined")


@patch("build_metabolights_register.EuropePMCClient")
@patch("build_metabolights_register.MetaboLightsClient")
def test_unresolved_when_neither_source_finds_anything(mock_mtbls_cls, mock_epmc_cls, tmp_path):
    mock_mtbls = MagicMock()
    mock_mtbls.fetch_all_study_accessions.return_value = ["MTBLS99999"]
    mock_mtbls.fetch_study_details.return_value = {"fake": "details"}
    mock_mtbls.extract_publication_info.return_value = (None, [])
    mock_mtbls_cls.return_value = mock_mtbls

    mock_epmc = MagicMock()
    mock_epmc.find_publications_mentioning_accession.return_value = []
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_metabolights_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute("SELECT resolution_source FROM dataset_register WHERE accession='MTBLS99999'").fetchone()
    assert row == ("unresolved",)
