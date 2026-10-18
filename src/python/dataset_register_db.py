"""
SQLite-backed register of dataset accession -> resolved publication
identifiers (PMID/PMCID/DOI), plus a cross-modality connections view.

Implements the schema from docs/plans/article-dataset-index-harvesting-plan.md:
- One flat `dataset_register` row per (accession, repository), independent
  of the other repositories (Stage A: building per-modality registers).
- An `article_connections` view that groups register rows across all
  repositories by a canonical join key (DOI preferred, then PMCID, then
  PMID) to surface cross-omics links (Stage B: finding connections amongst
  the registers) — a pure local query, no network calls.

CSV export (export_register_to_csv / export_connections_to_csv /
export_connections_long_to_csv) provides plain-text snapshots of the same
data alongside the SQLite file, for use in spreadsheets/pandas or sharing
without a SQLite reader.
"""

import csv
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS dataset_register (
    accession          TEXT NOT NULL,
    repository         TEXT NOT NULL,
    pmid               TEXT,
    pmcid              TEXT,
    doi                TEXT,
    resolution_source  TEXT NOT NULL,
    resolved_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (accession, repository)
);

CREATE INDEX IF NOT EXISTS idx_register_doi   ON dataset_register(doi);
CREATE INDEX IF NOT EXISTS idx_register_pmid  ON dataset_register(pmid);
CREATE INDEX IF NOT EXISTS idx_register_pmcid ON dataset_register(pmcid);

CREATE VIEW IF NOT EXISTS article_connections AS
SELECT
    COALESCE(doi, pmcid, pmid)                    AS join_key,
    COUNT(DISTINCT repository)                    AS modality_count,
    GROUP_CONCAT(DISTINCT repository)             AS repositories,
    GROUP_CONCAT(accession || ':' || repository)  AS accessions
FROM dataset_register
WHERE resolution_source != 'unresolved' AND COALESCE(doi, pmcid, pmid) IS NOT NULL
GROUP BY join_key;
"""

_DOI_URL_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    """
    Normalize a DOI to a canonical lowercase form with no URL prefix, so the
    same article's DOI matches regardless of which source supplied it.

    Args:
        doi: Raw DOI string, possibly prefixed with a doi.org URL

    Returns:
        Normalized DOI (e.g. "10.1080/21505594.2026.2739013"), or None if
        doi is falsy
    """
    if not doi:
        return None
    return _DOI_URL_PREFIX_RE.sub("", doi.strip()).lower()


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    Open (creating if needed) the register SQLite database and ensure the
    schema exists.

    Uses WAL journal mode and a generous busy timeout so the four Stage A
    register builders (pride/metabolights/workbench/gnps) can safely run
    concurrently against the same database file — they only ever write to
    disjoint rows (different `repository` values), but SQLite still
    serializes writers at the file level without this.

    Args:
        db_path: Filesystem path to the SQLite file

    Returns:
        An open sqlite3.Connection with the schema applied
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def is_resolved(conn: sqlite3.Connection, accession: str, repository: str) -> bool:
    """
    Check whether an (accession, repository) pair already has a register
    entry, for resumable harvests (skip work already done on a prior run).

    Args:
        conn: Open database connection
        accession: Dataset accession
        repository: Repository name

    Returns:
        True if a row already exists for this (accession, repository)
    """
    cur = conn.execute(
        "SELECT 1 FROM dataset_register WHERE accession = ? AND repository = ?",
        (accession, repository),
    )
    return cur.fetchone() is not None


def upsert_register_entry(conn: sqlite3.Connection, accession: str, repository: str,
                           pmid: Optional[str] = None, pmcid: Optional[str] = None,
                           doi: Optional[str] = None, resolution_source: str = "unresolved") -> None:
    """
    Insert or update a single register entry.

    Args:
        conn: Open database connection
        accession: Dataset accession (e.g. "PXD076809")
        repository: Repository name (e.g. "pride", "metabolights",
            "metabolomics_workbench", "massive")
        pmid: Resolved PubMed ID, if any
        pmcid: Resolved PubMed Central ID, if any
        doi: Resolved article DOI, if any (will be normalized) — this must
            be the ARTICLE's DOI, never a repository-minted dataset DOI
        resolution_source: "self_reported" | "text_mined" | "unresolved"
    """
    conn.execute(
        """
        INSERT INTO dataset_register (accession, repository, pmid, pmcid, doi, resolution_source, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(accession, repository) DO UPDATE SET
            pmid=excluded.pmid, pmcid=excluded.pmcid, doi=excluded.doi,
            resolution_source=excluded.resolution_source, resolved_at=excluded.resolved_at
        """,
        (accession, repository, pmid, pmcid, normalize_doi(doi), resolution_source),
    )
    conn.commit()


def register_stats(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Summarize register coverage per repository: total rows and how many were
    resolved via each source.

    Args:
        conn: Open database connection

    Returns:
        List of dicts: repository, total, self_reported, text_mined, unresolved
    """
    cur = conn.execute(
        """
        SELECT repository,
               COUNT(*) AS total,
               SUM(CASE WHEN resolution_source = 'self_reported' THEN 1 ELSE 0 END) AS self_reported,
               SUM(CASE WHEN resolution_source = 'text_mined' THEN 1 ELSE 0 END) AS text_mined,
               SUM(CASE WHEN resolution_source = 'unresolved' THEN 1 ELSE 0 END) AS unresolved
        FROM dataset_register
        GROUP BY repository
        ORDER BY repository
        """
    )
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def query_connections(conn: sqlite3.Connection, min_modalities: int = 2) -> List[Dict[str, Any]]:
    """
    Query the article_connections view for publications linking at least
    min_modalities distinct repositories (the cross-omics links Stage B
    exists to find).

    Args:
        conn: Open database connection
        min_modalities: Minimum distinct repository count to include
            (2 = cross-modality connections; 1 = every resolved entry)

    Returns:
        List of dicts: join_key, modality_count, repositories (sorted,
        comma-separated, so the same repository combination always produces
        an identical string regardless of row-insertion order), accessions
    """
    cur = conn.execute(
        "SELECT * FROM article_connections WHERE modality_count >= ? ORDER BY modality_count DESC",
        (min_modalities,),
    )
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    for row in rows:
        # GROUP_CONCAT(DISTINCT ...) does not guarantee a stable element
        # order across groups, so sort here for a consistent, de-duplicated
        # combination label (e.g. always "metabolights,pride", never also
        # "pride,metabolights" for the same combination).
        row["repositories"] = ",".join(sorted(row["repositories"].split(",")))
    return rows


def export_register_to_csv(conn: sqlite3.Connection, csv_path: str) -> int:
    """
    Export the full dataset_register table (one row per accession) to CSV.

    Args:
        conn: Open database connection
        csv_path: Output CSV file path

    Returns:
        Number of rows written
    """
    cur = conn.execute(
        "SELECT accession, repository, pmid, pmcid, doi, resolution_source, resolved_at "
        "FROM dataset_register ORDER BY repository, accession"
    )
    columns = [d[0] for d in cur.description]
    rows = cur.fetchall()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    logger.info(f"Exported {len(rows)} register rows to {csv_path}")
    return len(rows)


def export_connections_to_csv(conn: sqlite3.Connection, csv_path: str, min_modalities: int = 2) -> int:
    """
    Export the article_connections summary (one row per publication, with
    its repositories/accessions as delimited strings) to CSV. Convenient for
    a quick overview; see export_connections_long_to_csv() for a
    one-row-per-accession format that's easier to filter/pivot in a
    spreadsheet or pandas.

    Args:
        conn: Open database connection
        csv_path: Output CSV file path
        min_modalities: Minimum distinct repository count to include
            (2 = cross-modality connections; 1 = every resolved entry)

    Returns:
        Number of rows written
    """
    connections = query_connections(conn, min_modalities=min_modalities)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["join_key", "modality_count", "repositories", "accessions"])
        for row in connections:
            writer.writerow([row["join_key"], row["modality_count"], row["repositories"], row["accessions"]])

    logger.info(f"Exported {len(connections)} connection rows to {csv_path}")
    return len(connections)


def export_connections_long_to_csv(conn: sqlite3.Connection, csv_path: str, min_modalities: int = 2) -> int:
    """
    Export cross-modality connections in "long" format: one row per
    (join_key, accession, repository) triple rather than one row per
    publication with a delimited accession list. Easier to filter/join/pivot
    in a spreadsheet or pandas than export_connections_to_csv()'s summary form.

    Args:
        conn: Open database connection
        csv_path: Output CSV file path
        min_modalities: Minimum distinct repository count to include
            (2 = cross-modality connections; 1 = every resolved entry)

    Returns:
        Number of rows written (accession-level, not publication-level)
    """
    connections = query_connections(conn, min_modalities=min_modalities)

    row_count = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["join_key", "modality_count", "accession", "repository"])
        for row in connections:
            for accession_repo in row["accessions"].split(","):
                accession, repository = accession_repo.rsplit(":", 1)
                writer.writerow([row["join_key"], row["modality_count"], accession, repository])
                row_count += 1

    logger.info(f"Exported {row_count} accession-level connection rows to {csv_path}")
    return row_count


