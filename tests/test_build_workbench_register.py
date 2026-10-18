"""Unit tests for build_workbench_register.py's orchestration logic (mocked clients)."""

from unittest.mock import MagicMock, patch

from build_workbench_register import build_workbench_register
from dataset_register_db import get_connection


@patch("build_workbench_register.EuropePMCClient")
@patch("build_workbench_register.MetabolomicsWorkbenchClient")
def test_self_reported_mwtab_path_skips_europepmc(mock_mw_cls, mock_epmc_cls, tmp_path):
    mock_mw = MagicMock()
    mock_mw.fetch_all_study_ids.return_value = ["ST000006"]
    mock_mw.fetch_study_mwtab_text.return_value = "PR:PUBLICATIONS\t...doi: 10.1021/jf9019322..."
    mock_mw.extract_publication_info_from_mwtab.return_value = ("10.1021/jf9019322", "19588931")
    mock_mw_cls.return_value = mock_mw

    mock_epmc = MagicMock()
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_workbench_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT pmid, doi, resolution_source FROM dataset_register WHERE accession='ST000006'"
    ).fetchone()
    assert row == ("19588931", "10.1021/jf9019322", "self_reported")
    mock_epmc.find_publications_mentioning_accession.assert_not_called()


@patch("build_workbench_register.EuropePMCClient")
@patch("build_workbench_register.MetabolomicsWorkbenchClient")
def test_falls_back_to_europepmc_when_mwtab_has_no_publication(mock_mw_cls, mock_epmc_cls, tmp_path):
    mock_mw = MagicMock()
    mock_mw.fetch_all_study_ids.return_value = ["ST000002"]
    mock_mw.fetch_study_mwtab_text.return_value = "PR:PROJECT_TITLE\tTest\n"
    mock_mw.extract_publication_info_from_mwtab.return_value = (None, None)
    mock_mw_cls.return_value = mock_mw

    mock_epmc = MagicMock()
    mock_epmc.find_publications_mentioning_accession.return_value = [
        {"pmid": "1", "pmcid": "PMC1", "doi": "10.1/x"}
    ]
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_workbench_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT resolution_source FROM dataset_register WHERE accession='ST000002'"
    ).fetchone()
    assert row == ("text_mined",)


@patch("build_workbench_register.EuropePMCClient")
@patch("build_workbench_register.MetabolomicsWorkbenchClient")
def test_handles_missing_mwtab_text_gracefully(mock_mw_cls, mock_epmc_cls, tmp_path):
    mock_mw = MagicMock()
    mock_mw.fetch_all_study_ids.return_value = ["ST999999"]
    mock_mw.fetch_study_mwtab_text.return_value = None
    mock_mw_cls.return_value = mock_mw

    mock_epmc = MagicMock()
    mock_epmc.find_publications_mentioning_accession.return_value = []
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_workbench_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT resolution_source FROM dataset_register WHERE accession='ST999999'"
    ).fetchone()
    assert row == ("unresolved",)
    mock_mw.extract_publication_info_from_mwtab.assert_not_called()
