"""Unit tests for build_gnps_register.py's orchestration logic (mocked clients)."""

from unittest.mock import MagicMock, patch

from build_gnps_register import build_gnps_register
from dataset_register_db import get_connection


@patch("build_gnps_register.EuropePMCClient")
@patch("build_gnps_register.GNPSClient")
def test_resolves_via_europepmc_only(mock_gnps_cls, mock_epmc_cls, tmp_path):
    mock_gnps = MagicMock()
    mock_gnps.fetch_datasets.return_value = [{"accession": "MSV000078556", "repository": "MassIVE"}]
    mock_gnps_cls.return_value = mock_gnps

    mock_epmc = MagicMock()
    mock_epmc.find_publications_mentioning_accession.return_value = [
        {"pmid": "34793633", "pmcid": "PMC8932847", "doi": "10.1111/cts.13198"}
    ]
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_gnps_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT pmid, pmcid, doi, resolution_source FROM dataset_register WHERE accession='MSV000078556'"
    ).fetchone()
    assert row == ("34793633", "PMC8932847", "10.1111/cts.13198", "text_mined")


@patch("build_gnps_register.EuropePMCClient")
@patch("build_gnps_register.GNPSClient")
def test_unresolved_when_no_europepmc_hit(mock_gnps_cls, mock_epmc_cls, tmp_path):
    mock_gnps = MagicMock()
    mock_gnps.fetch_datasets.return_value = [{"accession": "MSV000999999", "repository": "MassIVE"}]
    mock_gnps_cls.return_value = mock_gnps

    mock_epmc = MagicMock()
    mock_epmc.find_publications_mentioning_accession.return_value = []
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_gnps_register(db_path, delay=0)

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT resolution_source FROM dataset_register WHERE accession='MSV000999999'"
    ).fetchone()
    assert row == ("unresolved",)


@patch("build_gnps_register.EuropePMCClient")
@patch("build_gnps_register.GNPSClient")
def test_skips_entries_without_an_accession(mock_gnps_cls, mock_epmc_cls, tmp_path):
    mock_gnps = MagicMock()
    mock_gnps.fetch_datasets.return_value = [{"repository": "MassIVE"}]  # no "accession" key
    mock_gnps_cls.return_value = mock_gnps

    mock_epmc = MagicMock()
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_gnps_register(db_path, delay=0)

    mock_epmc.find_publications_mentioning_accession.assert_not_called()


@patch("build_gnps_register.EuropePMCClient")
@patch("build_gnps_register.GNPSClient")
def test_maps_non_massive_repository_labels_correctly(mock_gnps_cls, mock_epmc_cls, tmp_path):
    """
    GNPS2's inventory also includes MetaboLights/Workbench-sourced datasets
    (confirmed empirically) — these must be tagged with the correct register
    repository, not hardcoded to "massive".
    """
    mock_gnps = MagicMock()
    mock_gnps.fetch_datasets.return_value = [
        {"accession": "MTBLS14236", "repository": "MetaboLights"},
        {"accession": "ST000006", "repository": "Workbench"},
    ]
    mock_gnps_cls.return_value = mock_gnps

    mock_epmc = MagicMock()
    mock_epmc.find_publications_mentioning_accession.return_value = []
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_gnps_register(db_path, delay=0)

    conn = get_connection(db_path)
    rows = {r[0]: r[1] for r in conn.execute("SELECT accession, repository FROM dataset_register")}
    assert rows["MTBLS14236"] == "metabolights"
    assert rows["ST000006"] == "metabolomics_workbench"


@patch("build_gnps_register.EuropePMCClient")
@patch("build_gnps_register.GNPSClient")
def test_skips_accession_already_resolved_under_correct_repository_by_another_harvester(
    mock_gnps_cls, mock_epmc_cls, tmp_path
):
    """
    An MTBLS accession already resolved by build_metabolights_register.py
    (tagged repository='metabolights') must be recognized as already done
    here too, instead of being reprocessed via a redundant Europe PMC call.
    """
    from dataset_register_db import upsert_register_entry

    db_path = str(tmp_path / "register.db")
    conn = get_connection(db_path)
    upsert_register_entry(conn, "MTBLS14236", "metabolights", pmid="42802602", resolution_source="text_mined")

    mock_gnps = MagicMock()
    mock_gnps.fetch_datasets.return_value = [{"accession": "MTBLS14236", "repository": "MetaboLights"}]
    mock_gnps_cls.return_value = mock_gnps

    mock_epmc = MagicMock()
    mock_epmc_cls.return_value = mock_epmc

    build_gnps_register(db_path, delay=0)

    mock_epmc.find_publications_mentioning_accession.assert_not_called()


@patch("build_gnps_register.EuropePMCClient")
@patch("build_gnps_register.GNPSClient")
def test_skips_entries_with_unmapped_repository_value(mock_gnps_cls, mock_epmc_cls, tmp_path):
    mock_gnps = MagicMock()
    mock_gnps.fetch_datasets.return_value = [{"accession": "XYZ123", "repository": "SomeOtherRepo"}]
    mock_gnps_cls.return_value = mock_gnps

    mock_epmc = MagicMock()
    mock_epmc_cls.return_value = mock_epmc

    db_path = str(tmp_path / "register.db")
    build_gnps_register(db_path, delay=0)

    conn = get_connection(db_path)
    assert conn.execute("SELECT COUNT(*) FROM dataset_register").fetchone()[0] == 0
    mock_epmc.find_publications_mentioning_accession.assert_not_called()
