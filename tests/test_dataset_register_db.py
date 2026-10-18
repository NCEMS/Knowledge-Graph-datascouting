"""Unit tests for dataset_register_db.py (SQLite register + connections view)."""

import csv

from dataset_register_db import (
    export_connections_long_to_csv,
    export_connections_to_csv,
    export_register_to_csv,
    get_connection,
    is_resolved,
    normalize_doi,
    query_connections,
    register_stats,
    upsert_register_entry,
)


def test_normalize_doi_strips_url_prefix_and_lowercases():
    assert normalize_doi("https://doi.org/10.1080/ABC") == "10.1080/abc"
    assert normalize_doi("http://dx.doi.org/10.1080/ABC") == "10.1080/abc"
    assert normalize_doi("10.1080/ABC") == "10.1080/abc"
    assert normalize_doi(None) is None
    assert normalize_doi("") is None


def test_upsert_and_is_resolved():
    conn = get_connection(":memory:")
    assert is_resolved(conn, "PXD1", "pride") is False

    upsert_register_entry(conn, "PXD1", "pride", pmid="1", doi="10.1/x", resolution_source="self_reported")

    assert is_resolved(conn, "PXD1", "pride") is True
    assert is_resolved(conn, "PXD1", "metabolights") is False  # same accession, different repository


def test_upsert_overwrites_existing_entry():
    conn = get_connection(":memory:")
    upsert_register_entry(conn, "PXD1", "pride", resolution_source="unresolved")
    upsert_register_entry(conn, "PXD1", "pride", pmid="42", doi="10.1/y", resolution_source="self_reported")

    row = conn.execute("SELECT pmid, doi, resolution_source FROM dataset_register WHERE accession='PXD1'").fetchone()
    assert row == ("42", "10.1/y", "self_reported")


def test_register_stats_groups_by_repository_and_source():
    conn = get_connection(":memory:")
    upsert_register_entry(conn, "PXD1", "pride", pmid="1", resolution_source="self_reported")
    upsert_register_entry(conn, "PXD2", "pride", resolution_source="unresolved")
    upsert_register_entry(conn, "MTBLS1", "metabolights", pmid="1", resolution_source="text_mined")

    stats = {row["repository"]: row for row in register_stats(conn)}

    assert stats["pride"]["total"] == 2
    assert stats["pride"]["self_reported"] == 1
    assert stats["pride"]["unresolved"] == 1
    assert stats["metabolights"]["text_mined"] == 1


def test_query_connections_finds_cross_modality_links():
    conn = get_connection(":memory:")
    upsert_register_entry(conn, "PXD076809", "pride", pmid="42802602",
                           doi="10.1080/21505594.2026.2739013", resolution_source="self_reported")
    upsert_register_entry(conn, "MTBLS14236", "metabolights", pmid="42802602", pmcid="PMC13625717",
                           doi="10.1080/21505594.2026.2739013", resolution_source="text_mined")
    upsert_register_entry(conn, "ST000001", "metabolomics_workbench", resolution_source="unresolved")

    connections = query_connections(conn, min_modalities=2)

    assert len(connections) == 1
    assert connections[0]["modality_count"] == 2
    assert connections[0]["repositories"] == "metabolights,pride"  # always sorted, order-independent


def test_query_connections_excludes_unresolved_and_single_modality():
    conn = get_connection(":memory:")
    upsert_register_entry(conn, "PXD1", "pride", pmid="1", resolution_source="self_reported")
    upsert_register_entry(conn, "PXD2", "pride", resolution_source="unresolved")

    assert query_connections(conn, min_modalities=2) == []
    # min_modalities=1 should still surface the single resolved entry (its own join_key group)
    single = query_connections(conn, min_modalities=1)
    assert len(single) == 1
    assert single[0]["modality_count"] == 1


def test_export_register_to_csv(tmp_path):
    conn = get_connection(":memory:")
    upsert_register_entry(conn, "PXD1", "pride", pmid="1", doi="10.1/x", resolution_source="self_reported")
    upsert_register_entry(conn, "MTBLS1", "metabolights", resolution_source="unresolved")

    csv_path = str(tmp_path / "register.csv")
    count = export_register_to_csv(conn, csv_path)

    assert count == 2
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    row_by_accession = {r["accession"]: r for r in rows}
    assert row_by_accession["PXD1"]["doi"] == "10.1/x"
    assert row_by_accession["PXD1"]["resolution_source"] == "self_reported"
    assert row_by_accession["MTBLS1"]["resolution_source"] == "unresolved"


def test_export_register_to_csv_empty_register(tmp_path):
    conn = get_connection(":memory:")
    csv_path = str(tmp_path / "register.csv")
    count = export_register_to_csv(conn, csv_path)

    assert count == 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == []  # header row written, zero data rows


def test_export_connections_to_csv(tmp_path):
    conn = get_connection(":memory:")
    upsert_register_entry(conn, "PXD076809", "pride", pmid="42802602",
                           doi="10.1080/21505594.2026.2739013", resolution_source="self_reported")
    upsert_register_entry(conn, "MTBLS14236", "metabolights", pmid="42802602",
                           doi="10.1080/21505594.2026.2739013", resolution_source="text_mined")

    csv_path = str(tmp_path / "connections.csv")
    count = export_connections_to_csv(conn, csv_path, min_modalities=2)

    assert count == 1
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["join_key"] == "10.1080/21505594.2026.2739013"
    assert rows[0]["modality_count"] == "2"
    assert rows[0]["repositories"] == "metabolights,pride"


def test_export_connections_long_to_csv_one_row_per_accession(tmp_path):
    conn = get_connection(":memory:")
    upsert_register_entry(conn, "PXD076809", "pride", pmid="42802602",
                           doi="10.1080/21505594.2026.2739013", resolution_source="self_reported")
    upsert_register_entry(conn, "MTBLS14236", "metabolights", pmid="42802602",
                           doi="10.1080/21505594.2026.2739013", resolution_source="text_mined")

    csv_path = str(tmp_path / "connections_long.csv")
    count = export_connections_long_to_csv(conn, csv_path, min_modalities=2)

    assert count == 2  # one row per accession, not per publication
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    accession_repo_pairs = {(r["accession"], r["repository"]) for r in rows}
    assert accession_repo_pairs == {("PXD076809", "pride"), ("MTBLS14236", "metabolights")}
    assert all(r["join_key"] == "10.1080/21505594.2026.2739013" for r in rows)

