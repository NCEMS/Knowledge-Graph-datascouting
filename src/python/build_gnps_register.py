"""
Stage A harvester: build the GNPS/MassIVE register.

Enumerates GNPS2's metabolomics-tagged dataset inventory (gnps_client.GNPSClient.fetch_datasets())
and resolves each accession's publication identifiers purely via Europe PMC
accession-mention search — GNPS2's dataset metadata has no publication field
at all (confirmed in prior scouting), so there is no self-reported tier for
this modality; every entry costs one Europe PMC call. This is the costliest
register to build (~13.5k calls) for comparatively low payoff per
docs/plans/article-dataset-index-harvesting-plan.md §5 — build it last.

IMPORTANT: GNPS2's inventory is itself a cross-repository index — it
includes not just native MassIVE/GNPS (MSV...) datasets but also
MetaboLights (MTBLS...) and Metabolomics Workbench (ST...) datasets it has
indexed from those repositories (confirmed empirically: their counts match
those repositories' own total study counts almost exactly). Each entry's own
"repository" field must be mapped to the correct register `repository`
label (not hardcoded to "massive"), so that accessions already covered by
build_metabolights_register.py / build_workbench_register.py are correctly
recognized as already-resolved and skipped here rather than reprocessed
under a wrong label.

Usage:
    python build_gnps_register.py [--db PATH] [--delay SECONDS] [--limit N]
"""

import argparse
import logging
import sys
import time

from dataset_register_db import get_connection, is_resolved, upsert_register_entry
from europepmc_client import EuropePMCClient
from gnps_client import GNPSClient

logger = logging.getLogger(__name__)

# Maps GNPS2's own "repository" field values to this project's register
# repository labels (see the module docstring on why this matters).
REPOSITORY_LABELS = {
    "MassIVE": "massive",
    "MetaboLights": "metabolights",
    "Workbench": "metabolomics_workbench",
}


def build_gnps_register(db_path: str, delay: float = 0.2, limit: int = None) -> None:
    """
    Resolve every GNPS2-indexed metabolomics dataset's publication
    identifiers via Europe PMC accession search and upsert them into the
    register. Idempotent/resumable: already-registered accessions (including
    ones already resolved by the MetaboLights/Workbench-specific harvesters,
    since GNPS2's inventory also includes those repositories' datasets) are
    skipped so an interrupted run can simply be restarted.

    Args:
        db_path: Path to the SQLite register database
        delay: Seconds to sleep between accessions (politeness toward
            Europe PMC's public API)
        limit: Optional cap on the number of datasets processed, for testing
    """
    conn = get_connection(db_path)
    client = GNPSClient()
    europepmc = EuropePMCClient()

    datasets = client.fetch_datasets()
    processed = 0
    text_mined = 0
    skipped = 0
    unmapped_repositories = set()

    for dataset in datasets:
        accession = dataset.get("accession")
        if not accession:
            continue

        repository = REPOSITORY_LABELS.get(dataset.get("repository"))
        if not repository:
            unmapped_repositories.add(dataset.get("repository"))
            continue

        if is_resolved(conn, accession, repository):
            skipped += 1
            continue

        pmid = pmcid = doi = None
        source = "unresolved"

        hits = europepmc.find_publications_mentioning_accession(accession)
        if hits:
            hit = hits[0]
            pmid, pmcid, doi = hit.get("pmid"), hit.get("pmcid"), hit.get("doi")
            source = "text_mined"
            text_mined += 1

        upsert_register_entry(conn, accession, repository, pmid=pmid, pmcid=pmcid, doi=doi,
                               resolution_source=source)
        processed += 1

        if processed % 250 == 0:
            logger.info(f"GNPS/MassIVE register: processed={processed} text_mined={text_mined} "
                        f"skipped={skipped} / {len(datasets)} total")

        if limit and processed >= limit:
            break

        time.sleep(delay)

    if unmapped_repositories:
        logger.warning(f"Skipped entries from unmapped GNPS2 repository values: {unmapped_repositories}")

    logger.info(f"GNPS/MassIVE register complete: processed={processed} text_mined={text_mined} skipped={skipped}")


def main():
    parser = argparse.ArgumentParser(description="Build the GNPS/MassIVE dataset register")
    parser.add_argument("--db", default="data/article_dataset_index.db", help="SQLite register database path")
    parser.add_argument("--delay", type=float, default=0.2, help="Seconds to sleep between accessions")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on datasets processed (for testing)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_gnps_register(args.db, delay=args.delay, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
