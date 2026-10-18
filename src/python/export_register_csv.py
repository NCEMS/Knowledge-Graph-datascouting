"""
Export the dataset_register_db.py SQLite register to CSV files.

Writes three CSVs into --output-dir:
  - dataset_register.csv          one row per accession (full register dump)
  - article_connections.csv       one row per publication linking >= min-modalities
                                   repositories (repositories/accessions as
                                   delimited strings)
  - article_connections_long.csv  one row per (publication, accession,
                                   repository) triple — easier to filter/pivot

Usage:
    python export_register_csv.py [--db PATH] [--output-dir DIR] [--min-modalities N]
"""

import argparse
import logging
import os
import sys

from dataset_register_db import (
    export_connections_long_to_csv,
    export_connections_to_csv,
    export_register_to_csv,
    get_connection,
)

logger = logging.getLogger(__name__)


def export_all(db_path: str, output_dir: str, min_modalities: int = 2) -> None:
    """
    Export the register and connections views to CSV files in output_dir.

    Args:
        db_path: Path to the SQLite register database
        output_dir: Directory to write CSV files into (created if missing)
        min_modalities: Minimum distinct repository count for the
            connections exports (2 = cross-modality connections only)
    """
    os.makedirs(output_dir, exist_ok=True)
    conn = get_connection(db_path)

    register_csv = os.path.join(output_dir, "dataset_register.csv")
    connections_csv = os.path.join(output_dir, "article_connections.csv")
    connections_long_csv = os.path.join(output_dir, "article_connections_long.csv")

    register_count = export_register_to_csv(conn, register_csv)
    connections_count = export_connections_to_csv(conn, connections_csv, min_modalities=min_modalities)
    long_count = export_connections_long_to_csv(conn, connections_long_csv, min_modalities=min_modalities)

    logger.info(f"Export complete: {register_count} register rows, {connections_count} connections, "
                f"{long_count} connection accession rows -> {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Export the dataset register to CSV files")
    parser.add_argument("--db", default="data/article_dataset_index.db", help="SQLite register database path")
    parser.add_argument("--output-dir", default="data/csv", help="Directory to write CSV files into")
    parser.add_argument("--min-modalities", type=int, default=2,
                        help="Minimum distinct repository count for connections exports")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    export_all(args.db, args.output_dir, min_modalities=args.min_modalities)


if __name__ == "__main__":
    sys.exit(main())
