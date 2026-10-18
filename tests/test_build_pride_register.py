"""Unit tests for build_pride_register.py's orchestration logic (mocked PrideClient)."""

from unittest.mock import MagicMock, patch

from build_pride_register import build_pride_register
from dataset_register_db import get_connection, register_stats


@patch("build_pride_register.time.sleep")  # skip real sleeps in retry/page-failure paths
@patch("build_pride_register.PrideClient")
def test_build_pride_register_classifies_self_reported_and_unresolved(mock_client_cls, mock_sleep, tmp_path):
    mock_client = MagicMock()
    mock_client.fetch_projects_page.side_effect = [
        [
            {"accession": "PXD1", "references": ["x--pubMed:111--doi: 10.1/a"]},
            {"accession": "PXD2", "references": []},
        ],
        [],
    ]
    mock_client.extract_publication_info_from_search_summary.side_effect = [
        ("10.1/a", ["111"]),
        (None, []),
    ]
    mock_client_cls.return_value = mock_client

    db_path = str(tmp_path / "register.db")
    build_pride_register(db_path, page_size=100)

    conn = get_connection(db_path)
    rows = {r[0]: r for r in conn.execute("SELECT accession, pmid, doi, resolution_source FROM dataset_register")}
    assert rows["PXD1"][1:] == ("111", "10.1/a", "self_reported")
    assert rows["PXD2"][1:] == (None, None, "unresolved")


@patch("build_pride_register.time.sleep")
@patch("build_pride_register.PrideClient")
def test_build_pride_register_skips_already_resolved(mock_client_cls, mock_sleep, tmp_path):
    mock_client = MagicMock()
    mock_client.fetch_projects_page.side_effect = [[{"accession": "PXD1", "references": []}], []] * 2
    mock_client.extract_publication_info_from_search_summary.return_value = (None, [])
    mock_client_cls.return_value = mock_client

    db_path = str(tmp_path / "register.db")
    build_pride_register(db_path)  # first run
    build_pride_register(db_path)  # second run should skip PXD1 entirely

    # extracted once on the first run; second run's page re-fetch is skipped via is_resolved()
    assert mock_client.extract_publication_info_from_search_summary.call_count == 1


@patch("build_pride_register.time.sleep")
@patch("build_pride_register.PrideClient")
def test_build_pride_register_respects_limit(mock_client_cls, mock_sleep, tmp_path):
    mock_client = MagicMock()
    mock_client.fetch_projects_page.return_value = [
        {"accession": f"PXD{i}", "references": []} for i in range(5)
    ]
    mock_client.extract_publication_info_from_search_summary.return_value = (None, [])
    mock_client_cls.return_value = mock_client

    db_path = str(tmp_path / "register.db")
    build_pride_register(db_path, limit=2)

    conn = get_connection(db_path)
    stats = register_stats(conn)
    assert stats[0]["total"] == 2


@patch("build_pride_register.time.sleep")
@patch("build_pride_register.PrideClient")
def test_build_pride_register_survives_a_transient_page_failure(mock_client_cls, mock_sleep, tmp_path):
    """A single bad page (even after its own retries) must not abort the whole sweep."""
    mock_client = MagicMock()
    mock_client.fetch_projects_page.side_effect = [
        [{"accession": "PXD1", "references": []}],  # page 0: ok
        Exception("boom"), Exception("boom"), Exception("boom"),  # page 1: fails every retry
        [{"accession": "PXD2", "references": []}],  # page 2: ok, sweep continues
        [],  # page 3: done
    ]
    mock_client.extract_publication_info_from_search_summary.return_value = (None, [])
    mock_client_cls.return_value = mock_client

    db_path = str(tmp_path / "register.db")
    build_pride_register(db_path)

    conn = get_connection(db_path)
    accessions = {r[0] for r in conn.execute("SELECT accession FROM dataset_register")}
    assert accessions == {"PXD1", "PXD2"}


@patch("build_pride_register.time.sleep")
@patch("build_pride_register.PrideClient")
def test_build_pride_register_stops_after_consecutive_page_failures(mock_client_cls, mock_sleep, tmp_path):
    """Repeated failures should be treated as having run off the end of pagination, not loop forever."""
    mock_client = MagicMock()
    mock_client.fetch_projects_page.side_effect = Exception("always fails")
    mock_client_cls.return_value = mock_client

    db_path = str(tmp_path / "register.db")
    build_pride_register(db_path)  # must return, not hang

    conn = get_connection(db_path)
    stats = register_stats(conn)
    assert stats == []  # nothing was ever successfully fetched
