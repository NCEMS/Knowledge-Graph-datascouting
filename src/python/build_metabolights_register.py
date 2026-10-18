"""
Stage A harvester: build the MetaboLights register.

Enumerates all public MetaboLights studies (metabolights_client.fetch_all_study_accessions())
and resolves each one's publication identifiers: self-reported ISA
publications first, falling back to Europe PMC accession-mention search when
the study's own metadata has none (MetaboLights self-reporting is
inconsistent — see docs/plans/article-dataset-index-harvesting-plan.md §3).

Usage:
    python build_metabolights_register.py [--db PATH] [--delay SECONDS] [--limit N]
"""

import argparse
import logging
import sys
import time

from dataset_register_db import get_connection, is_resolved, upsert_register_entry
from europepmc_client import EuropePMCClient
from metabolights_client import MetaboLightsClient

logger = logging.getLogger(__name__)

REPOSITORY = "metabolights"


def build_metabolights_register(db_path: str, delay: float = 0.2, limit: int = None) -> None:
    """
    Resolve every public MetaboLights study's publication identifiers and
    upsert them into the register. Idempotent/resumable: already-registered
    accessions are skipped so an interrupted run can simply be restarted.

    Args:
        db_path: Path to the SQLite register database
        delay: Seconds to sleep between studies (politeness toward
            MetaboLights' and Europe PMC's public APIs)
        limit: Optional cap on the number of studies processed, for testing
    """
    conn = get_connection(db_path)
    client = MetaboLightsClient()
    europepmc = EuropePMCClient()

    accessions = client.fetch_all_study_accessions()
    processed = 0
    self_reported = 0
    text_mined = 0
    skipped = 0

    for accession in accessions:
        if is_resolved(conn, accession, REPOSITORY):
            skipped += 1
            continue

        pmid = pmcid = doi = None
        source = "unresolved"

        details = client.fetch_study_details(accession)
        if details:
            doi, pmids = client.extract_publication_info(details)
            pmid = pmids[0] if pmids else None
            if doi or pmid:
                source = "self_reported"
                self_reported += 1

        if source == "unresolved":
            hits = europepmc.find_publications_mentioning_accession(accession)
            if hits:
                hit = hits[0]
                pmid, pmcid, doi = hit.get("pmid"), hit.get("pmcid"), hit.get("doi")
                source = "text_mined"
                text_mined += 1

        upsert_register_entry(conn, accession, REPOSITORY, pmid=pmid, pmcid=pmcid, doi=doi,
                               resolution_source=source)
        processed += 1

        if processed % 100 == 0:
            logger.info(f"MetaboLights register: processed={processed} self_reported={self_reported} "
                        f"text_mined={text_mined} skipped={skipped} / {len(accessions)} total")

        if limit and processed >= limit:
            break

        time.sleep(delay)

    logger.info(f"MetaboLights register complete: processed={processed} self_reported={self_reported} "
                f"text_mined={text_mined} skipped={skipped}")


def main():
    parser = argparse.ArgumentParser(description="Build the MetaboLights dataset register")
    parser.add_argument("--db", default="data/article_dataset_index.db", help="SQLite register database path")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds to sleep between studies")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on studies processed (for testing)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_metabolights_register(args.db, delay=args.delay, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
