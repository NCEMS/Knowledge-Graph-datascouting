"""Unit tests for export_register_csv.py's orchestration logic."""

import csv
import os

from dataset_register_db import get_connection, upsert_register_entry
from export_register_csv import export_all


def test_export_all_writes_three_csv_files(tmp_path):
    db_path = str(tmp_path / "register.db")
    conn = get_connection(db_path)
    upsert_register_entry(conn, "PXD076809", "pride", pmid="42802602",
                           doi="10.1080/21505594.2026.2739013", resolution_source="self_reported")
    upsert_register_entry(conn, "MTBLS14236", "metabolights", pmid="42802602",
                           doi="10.1080/21505594.2026.2739013", resolution_source="text_mined")
    upsert_register_entry(conn, "ST000001", "metabolomics_workbench", resolution_source="unresolved")

    output_dir = str(tmp_path / "csv_out")
    export_all(db_path, output_dir, min_modalities=2)

    register_csv = os.path.join(output_dir, "dataset_register.csv")
    connections_csv = os.path.join(output_dir, "article_connections.csv")
    connections_long_csv = os.path.join(output_dir, "article_connections_long.csv")

    assert os.path.exists(register_csv)
    assert os.path.exists(connections_csv)
    assert os.path.exists(connections_long_csv)

    with open(register_csv, newline="", encoding="utf-8") as f:
        register_rows = list(csv.DictReader(f))
    assert len(register_rows) == 3  # all accessions, including the unresolved one

    with open(connections_csv, newline="", encoding="utf-8") as f:
        connection_rows = list(csv.DictReader(f))
    assert len(connection_rows) == 1  # only the 2-modality link; ST000001 is unresolved


def test_export_all_creates_missing_output_dir(tmp_path):
    db_path = str(tmp_path / "register.db")
    get_connection(db_path).close()

    output_dir = str(tmp_path / "nested" / "csv_out")
    assert not os.path.exists(output_dir)

    export_all(db_path, output_dir)

    assert os.path.exists(os.path.join(output_dir, "dataset_register.csv"))
