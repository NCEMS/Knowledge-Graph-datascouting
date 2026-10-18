"""
Cross-repository dataset linking module.

Implements the identifier-resolution hierarchy from the metabolomics data
scouting work (see docs/plans/metabolomics-clients-implementation-plan.md
and the follow-up universal-identifier discussion):

  1. Use a dataset's own repository-reported publication identifiers
     (PMID/PMCID/DOI) when present — e.g. pride_client.extract_publication_info()
     or metabolights_client.extract_publication_info().
  2. If absent, recover the publication via Europe PMC's full-text
     accession-mention index. Repository self-reporting is inconsistent in
     practice — e.g. MetaboLights study MTBLS14236 has an empty publications
     list in its own API, even though it is explicitly cited (alongside
     PRIDE's PXD076809) in PMC13625717. Europe PMC's text-mining recovers
     that link where the repository's own metadata does not have it.
  3. Once a canonical publication (PMID/PMCID/DOI) is known, text-mine its
     full text for *companion* accessions in other repositories, surfacing
     cross-omics links (proteomics dataset <-> companion metabolomics
     dataset cited in the same paper).

Each dataset keeps its own native accession as its identity; the publication
is treated as a bridging entity/edge for cross-repository linking, not as a
replacement primary key.
"""

import logging
from typing import Dict, List, Optional

from europepmc_client import EuropePMCClient

logger = logging.getLogger(__name__)


class PublicationIdentity:
    """A canonical publication identity (PMID/PMCID/DOI) plus how it was resolved."""

    def __init__(self, pmid: Optional[str] = None, pmcid: Optional[str] = None,
                 doi: Optional[str] = None, source: str = "unresolved"):
        """
        Args:
            pmid: PubMed ID
            pmcid: PubMed Central ID
            doi: Digital Object Identifier
            source: "self_reported" (came from the dataset's own repository
                metadata), "text_mined" (recovered via Europe PMC), or
                "unresolved" (no publication found)
        """
        self.pmid = pmid
        self.pmcid = pmcid
        self.doi = doi
        self.source = source

    def is_known(self) -> bool:
        """True if any identifier (pmid/pmcid/doi) was resolved"""
        return bool(self.pmid or self.pmcid or self.doi)

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {"pmid": self.pmid, "pmcid": self.pmcid, "doi": self.doi, "source": self.source}

    def __repr__(self) -> str:
        return f"PublicationIdentity(pmid={self.pmid!r}, pmcid={self.pmcid!r}, doi={self.doi!r}, source={self.source!r})"


class DatasetLink:
    """The result of resolving one dataset's publication and companion accessions."""

    def __init__(self, accession: str, repository: str, publication: PublicationIdentity,
                 companion_accessions: Optional[Dict[str, List[str]]] = None):
        """
        Args:
            accession: Native dataset accession (e.g. "PXD076809")
            repository: Repository name (e.g. "pride", "metabolights")
            publication: Resolved PublicationIdentity
            companion_accessions: Dict of repository name -> accessions found
                in the publication's full text, excluding this dataset itself
        """
        self.accession = accession
        self.repository = repository
        self.publication = publication
        self.companion_accessions = companion_accessions or {}

    def to_dict(self) -> Dict:
        return {
            "accession": self.accession,
            "repository": self.repository,
            "publication": self.publication.to_dict(),
            "companion_accessions": self.companion_accessions,
        }

    def __repr__(self) -> str:
        return (f"DatasetLink(accession={self.accession!r}, repository={self.repository!r}, "
                f"publication={self.publication!r}, companion_accessions={self.companion_accessions!r})")


class DatasetLinker:
    """Resolves the publication that bridges a dataset accession to companion accessions in other repositories"""

    def __init__(self, europepmc_client: Optional[EuropePMCClient] = None):
        """
        Args:
            europepmc_client: Optional pre-configured EuropePMCClient
                (a default one is created if not provided)
        """
        self.europepmc = europepmc_client or EuropePMCClient()

    def resolve_publication(self, accession: str, repository: str,
                             self_reported_doi: Optional[str] = None,
                             self_reported_pmids: Optional[List[str]] = None) -> PublicationIdentity:
        """
        Resolve a dataset's canonical publication identity, preferring the
        repository's own self-reported metadata and falling back to Europe
        PMC's full-text accession-mention index.

        Args:
            accession: Native dataset accession (e.g. "PXD076809")
            repository: Repository name, for logging only
            self_reported_doi: DOI already extracted from the repository's
                own metadata (e.g. via pride_client.extract_publication_info)
            self_reported_pmids: PMIDs already extracted from the repository's
                own metadata

        Returns:
            PublicationIdentity with source="self_reported" if the repository
            already supplied one; source="text_mined" if recovered via
            Europe PMC; source="unresolved" if neither found anything
        """
        if self_reported_pmids or self_reported_doi:
            pmid = self_reported_pmids[0] if self_reported_pmids else None
            logger.info(f"{repository} {accession}: using self-reported publication "
                        f"(pmid={pmid}, doi={self_reported_doi})")
            return PublicationIdentity(pmid=pmid, doi=self_reported_doi, source="self_reported")

        logger.info(f"{repository} {accession}: no self-reported publication, text-mining Europe PMC")
        hits = self.europepmc.find_publications_mentioning_accession(accession)
        if hits:
            hit = hits[0]
            logger.info(f"{repository} {accession}: recovered publication via Europe PMC "
                        f"(pmid={hit.get('pmid')}, pmcid={hit.get('pmcid')})")
            return PublicationIdentity(pmid=hit.get("pmid"), pmcid=hit.get("pmcid"),
                                        doi=hit.get("doi"), source="text_mined")

        logger.warning(f"{repository} {accession}: no publication found (self-reported or text-mined)")
        return PublicationIdentity(source="unresolved")

    def find_companion_datasets(self, publication: PublicationIdentity) -> Dict[str, List[str]]:
        """
        Given a resolved publication, find companion dataset accessions
        across repositories by text-mining its full text.

        Args:
            publication: A PublicationIdentity. A pmcid is required for
                full-text access; if only pmid/doi is known, this method
                first attempts to resolve the pmcid via Europe PMC.

        Returns:
            Dict of repository name -> list of accessions found in the
            publication's full text. Empty dict if no pmcid could be
            resolved or full text could not be fetched (e.g. non-OA article).
        """
        if not publication.pmcid and publication.pmid:
            record = self.europepmc.fetch_publication(pmid=publication.pmid)
            if record and record.get("pmcid"):
                publication.pmcid = record["pmcid"]

        if not publication.pmcid:
            logger.warning("find_companion_datasets requires a pmcid (full text access); none available")
            return {}

        return self.europepmc.find_accessions_in_publication(publication.pmcid)

    def link(self, accession: str, repository: str,
             self_reported_doi: Optional[str] = None,
             self_reported_pmids: Optional[List[str]] = None) -> DatasetLink:
        """
        Full resolution pipeline for a single dataset: resolve its
        publication, then discover companion accessions across repositories.

        Args:
            accession: Native dataset accession (e.g. "PXD076809")
            repository: Repository name — use the same keys as
                europepmc_client.ACCESSION_PATTERNS ("pride", "metabolights",
                "metabolomics_workbench", "massive", ...) so this dataset is
                correctly excluded from its own companion-accession results
            self_reported_doi: DOI already extracted from the repository API
            self_reported_pmids: PMIDs already extracted from the repository API

        Returns:
            DatasetLink with the resolved publication and companion
            accessions (excluding the input accession/repository itself)
        """
        publication = self.resolve_publication(accession, repository, self_reported_doi, self_reported_pmids)
        companions: Dict[str, List[str]] = {}

        if publication.is_known():
            companions = self.find_companion_datasets(publication)
            if repository in companions:
                companions[repository] = [a for a in companions[repository] if a != accession]
                if not companions[repository]:
                    del companions[repository]

        return DatasetLink(accession=accession, repository=repository,
                            publication=publication, companion_accessions=companions)
