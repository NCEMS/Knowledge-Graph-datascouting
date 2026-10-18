"""
Stage A harvester: build the PRIDE register.

Sweeps PRIDE's bulk project-listing endpoint (pride_client.fetch_projects_page())
and records each project's self-reported publication identifiers into
dataset_register_db.py. See docs/plans/article-dataset-index-harvesting-plan.md
§5 ("PRIDE register").

Usage:
    python build_pride_register.py [--db PATH] [--page-size N] [--limit N]
"""

import argparse
import logging
import sys
import time

from dataset_register_db import get_connection, is_resolved, upsert_register_entry
from pride_client import PrideClient

logger = logging.getLogger(__name__)

REPOSITORY = "pride"
MAX_PAGE_RETRIES = 3
PAGE_RETRY_WAIT_SECONDS = 5


def _fetch_page_with_resilience(client: PrideClient, page: int, page_size: int):
    """
    Fetch one page of projects, retrying a few times with a longer pause on
    failure (on top of pride_client's own internal retry/backoff) so a
    single transient server hiccup can't abort the entire multi-thousand-page
    sweep. Returns an empty list (treated as "no more pages") if every
    attempt fails, logging the page as skipped rather than crashing — a
    subsequent re-run will pick it back up since this harvester paginates
    from page 0 every time and skips only already-registered accessions.
    """
    for attempt in range(MAX_PAGE_RETRIES):
        try:
            return client.fetch_projects_page(page, page_size=page_size)
        except Exception as e:
            logger.warning(f"Page {page} fetch failed (attempt {attempt + 1}/{MAX_PAGE_RETRIES}): {e}")
            time.sleep(PAGE_RETRY_WAIT_SECONDS)

    logger.error(f"Page {page} failed after {MAX_PAGE_RETRIES} attempts; skipping it for this run")
    return None


def build_pride_register(db_path: str, page_size: int = 100, limit: int = None) -> None:
    """
    Sweep all public PRIDE projects and upsert each one's self-reported
    publication identifiers (or an "unresolved" row if it has none) into the
    register. Idempotent/resumable: already-registered accessions are
    skipped so an interrupted run can simply be restarted, and a page that
    fails even after retries is logged and skipped rather than aborting the
    whole sweep (a later re-run will revisit it).

    Args:
        db_path: Path to the SQLite register database
        page_size: PRIDE search results per page
        limit: Optional cap on the number of projects processed, for testing
    """
    conn = get_connection(db_path)
    client = PrideClient()

    processed = 0
    resolved = 0
    skipped = 0
    page = 0
    consecutive_page_failures = 0
    max_consecutive_page_failures = 3  # treat repeated failures as "ran off the end of pagination"

    while True:
        projects = _fetch_page_with_resilience(client, page, page_size)
        if projects is None:
            consecutive_page_failures += 1
            if consecutive_page_failures >= max_consecutive_page_failures:
                logger.error(f"{max_consecutive_page_failures} consecutive page failures; "
                             f"stopping (page {page} onward can be swept by a future re-run)")
                break
            page += 1
            continue

        consecutive_page_failures = 0
        if not projects:
            break

        for project in projects:
            accession = project.get("accession")
            if not accession:
                continue

            if is_resolved(conn, accession, REPOSITORY):
                skipped += 1
                continue

            doi, pmids = client.extract_publication_info_from_search_summary(project)
            pmid = pmids[0] if pmids else None
            source = "self_reported" if (doi or pmid) else "unresolved"
            if source == "self_reported":
                resolved += 1

            upsert_register_entry(conn, accession, REPOSITORY, pmid=pmid, doi=doi, resolution_source=source)
            processed += 1

            if processed % 500 == 0:
                logger.info(f"PRIDE register: processed={processed} resolved={resolved} skipped={skipped}")

            if limit and processed >= limit:
                logger.info(f"PRIDE register complete (limit reached): processed={processed} "
                            f"resolved={resolved} skipped={skipped}")
                return

        page += 1

    logger.info(f"PRIDE register complete: processed={processed} resolved={resolved} skipped={skipped}")


def main():
    parser = argparse.ArgumentParser(description="Build the PRIDE dataset register")
    parser.add_argument("--db", default="data/article_dataset_index.db", help="SQLite register database path")
    parser.add_argument("--page-size", type=int, default=100, help="PRIDE search results per page")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on projects processed (for testing)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_pride_register(args.db, page_size=args.page_size, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
