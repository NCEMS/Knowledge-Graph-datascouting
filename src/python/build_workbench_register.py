"""
Stage A harvester: build the Metabolomics Workbench register.

Enumerates all public MW studies (metabolomics_workbench_client.fetch_all_study_ids())
and resolves each one's publication identifiers: the legacy mwTab text
dump's free-text "PR:PUBLICATIONS" field first (the only place MW exposes a
study's publication — its clean JSON endpoints never do), falling back to
Europe PMC accession-mention search when that's empty/unparseable. See
docs/plans/article-dataset-index-harvesting-plan.md §5 ("Metabolomics
Workbench register").

Usage:
    python build_workbench_register.py [--db PATH] [--delay SECONDS] [--limit N]
"""

import argparse
import logging
import sys
import time

from dataset_register_db import get_connection, is_resolved, upsert_register_entry
from europepmc_client import EuropePMCClient
from metabolomics_workbench_client import MetabolomicsWorkbenchClient

logger = logging.getLogger(__name__)

REPOSITORY = "metabolomics_workbench"


def build_workbench_register(db_path: str, delay: float = 0.2, limit: int = None) -> None:
    """
    Resolve every public Metabolomics Workbench study's publication
    identifiers and upsert them into the register. Idempotent/resumable:
    already-registered accessions are skipped so an interrupted run can
    simply be restarted.

    Args:
        db_path: Path to the SQLite register database
        delay: Seconds to sleep between studies (politeness toward MW's and
            Europe PMC's public APIs)
        limit: Optional cap on the number of studies processed, for testing
    """
    conn = get_connection(db_path)
    client = MetabolomicsWorkbenchClient()
    europepmc = EuropePMCClient()

    study_ids = client.fetch_all_study_ids()
    processed = 0
    self_reported = 0
    text_mined = 0
    skipped = 0

    for study_id in study_ids:
        if is_resolved(conn, study_id, REPOSITORY):
            skipped += 1
            continue

        pmid = pmcid = doi = None
        source = "unresolved"

        mwtab_text = client.fetch_study_mwtab_text(study_id)
        if mwtab_text:
            doi, pmid = client.extract_publication_info_from_mwtab(mwtab_text)
            if doi or pmid:
                source = "self_reported"
                self_reported += 1

        if source == "unresolved":
            hits = europepmc.find_publications_mentioning_accession(study_id)
            if hits:
                hit = hits[0]
                pmid, pmcid, doi = hit.get("pmid"), hit.get("pmcid"), hit.get("doi")
                source = "text_mined"
                text_mined += 1

        upsert_register_entry(conn, study_id, REPOSITORY, pmid=pmid, pmcid=pmcid, doi=doi,
                               resolution_source=source)
        processed += 1

        if processed % 100 == 0:
            logger.info(f"Workbench register: processed={processed} self_reported={self_reported} "
                        f"text_mined={text_mined} skipped={skipped} / {len(study_ids)} total")

        if limit and processed >= limit:
            break

        time.sleep(delay)

    logger.info(f"Workbench register complete: processed={processed} self_reported={self_reported} "
                f"text_mined={text_mined} skipped={skipped}")


def main():
    parser = argparse.ArgumentParser(description="Build the Metabolomics Workbench dataset register")
    parser.add_argument("--db", default="data/article_dataset_index.db", help="SQLite register database path")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds to sleep between studies")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on studies processed (for testing)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_workbench_register(args.db, delay=args.delay, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
