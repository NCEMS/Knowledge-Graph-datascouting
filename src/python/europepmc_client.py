"""
Europe PMC API Client for resolving the publication that connects dataset
accessions across repositories (PRIDE/PXD, MetaboLights/MTBLS, Metabolomics
Workbench/ST, GNPS/MassIVE/MSV, ...).

Why this client exists: repository self-reported PMID/DOI fields are
inconsistently populated (confirmed empirically — e.g. MetaboLights study
MTBLS14236 has an empty publications list in its own API, even though a real
paper, PMC13625717, explicitly cites it alongside PXD076809). Europe PMC
already full-text-indexes accession mentions across >40 biological databases,
so it can recover cross-repository links that a single repository's own
metadata is missing.

API reference: https://europepmc.org/RestfulWebService
"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests

from http_utils import get_json_with_retry, get_text_with_retry

logger = logging.getLogger(__name__)

# Dataset-accession patterns recognized in free text / full text, keyed by
# the repository they identify. Used by find_accessions_in_publication().
# These are deliberately conservative (fixed-width digit runs where the
# source enforces one) to limit false positives in free text.
ACCESSION_PATTERNS: Dict[str, str] = {
    "pride": r"\bPXD\d{6,}\b",
    "metabolights": r"\bMTBLS\d+\b",
    "metabolomics_workbench": r"\bST\d{6}\b",
    "massive": r"\bMSV\d{9}\b",
    "bioproject": r"\bPRJ[EDN][A-Z]\d+\b",
    "biosample": r"\bSAM[EDN]?[A-Z]?\d+\b",
}


class EuropePMCClient:
    """Client for Europe PMC REST API interactions"""

    def __init__(self, base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest", timeout: int = 30):
        """
        Initialize Europe PMC client

        Args:
            base_url: Europe PMC REST API base URL
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get_json(self, url: str, params: Optional[Dict] = None, max_retries: int = 5) -> Optional[Any]:
        """GET JSON with retry logic and exponential backoff"""
        return get_json_with_retry(self.session, url, params=params, timeout=self.timeout, max_retries=max_retries)

    def _get_text(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None,
                  max_retries: int = 5) -> Optional[str]:
        """GET raw text with retry logic and exponential backoff"""
        return get_text_with_retry(self.session, url, params=params, headers=headers,
                                    timeout=self.timeout, max_retries=max_retries)

    def search(self, query: str, result_type: str = "core", page_size: int = 25) -> List[Dict[str, Any]]:
        """
        Run a free-text Europe PMC search.

        Args:
            query: Europe PMC query string (supports quoted phrases, field
                queries like "ext_id:42802602 AND src:MED", boolean AND/OR)
            result_type: "core" (includes pmid/pmcid/doi/abstract) or "lite"
            page_size: Max results to return (single page, no cursor paging)

        Returns:
            List of result dicts
        """
        params = {"query": query, "format": "json", "resultType": result_type, "pageSize": page_size}
        logger.info(f"Europe PMC search: {query!r}")
        data = self._get_json(f"{self.base_url}/search", params=params)
        if not data:
            return []
        results = data.get("resultList", {}).get("result", [])
        logger.info(f"Europe PMC search '{query}' matched {data.get('hitCount')} (returned {len(results)})")
        return results

    def fetch_publication(self, pmid: Optional[str] = None, pmcid: Optional[str] = None,
                           doi: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Fetch a single publication's core record (title, abstract, pmid,
        pmcid, doi) by whichever identifier is available.

        Args:
            pmid: PubMed ID
            pmcid: PubMed Central ID (e.g. "PMC13625717")
            doi: Digital Object Identifier

        Returns:
            Core result dict, or None if not found / no identifier given
        """
        if pmid:
            query = f"ext_id:{pmid} AND src:MED"
        elif pmcid:
            query = f"pmcid:{pmcid}"
        elif doi:
            query = f'doi:"{doi}"'
        else:
            logger.error("fetch_publication requires pmid, pmcid, or doi")
            return None

        results = self.search(query, result_type="core", page_size=1)
        return results[0] if results else None

    def find_publications_mentioning_accession(self, accession: str, page_size: int = 25) -> List[Dict[str, Any]]:
        """
        Find publications whose text mentions a given dataset accession
        (e.g. "PXD076809", "MTBLS14236"). This recovers links that a
        repository's own self-reported publication metadata may be missing.

        Args:
            accession: Dataset accession to search for, as an exact phrase
            page_size: Max results to return

        Returns:
            List of core result dicts (pmid, pmcid, doi, title, ...)
        """
        return self.search(f'"{accession}"', result_type="core", page_size=page_size)

    def fetch_full_text_xml(self, pmcid: str) -> Optional[str]:
        """
        Fetch a PMC article's full text as XML (Europe PMC's OA full-text
        endpoint, distinct from NCBI's BioC format used by pmc_client.py).

        Args:
            pmcid: PubMed Central ID (e.g. "PMC13625717")

        Returns:
            Raw XML text, or None if unavailable/failed
        """
        logger.info(f"Fetching Europe PMC full-text XML for {pmcid}")
        try:
            # Override the session's default "Accept: application/json" header —
            # this endpoint only returns XML and 406s otherwise.
            return self._get_text(f"{self.base_url}/{pmcid}/fullTextXML", headers={"Accept": "application/xml"})
        except requests.RequestException as e:
            # Full text is routinely unavailable for non-open-access articles
            # (404) — this is an expected outcome, not a transient failure.
            logger.warning(f"Full text unavailable for {pmcid}: {e}")
            return None

    def find_accessions_in_publication(self, pmcid: str,
                                        patterns: Optional[Dict[str, str]] = None) -> Dict[str, List[str]]:
        """
        Text-mine a publication's full text for dataset accessions across
        repositories, to discover companion datasets a single repository's
        metadata doesn't cross-reference (e.g. a proteomics PXD accession
        and its companion metabolomics MTBLS accession cited in the same
        paper).

        Args:
            pmcid: PubMed Central ID (full text must be OA to succeed)
            patterns: Optional override of ACCESSION_PATTERNS (repo name ->
                regex). Defaults to ACCESSION_PATTERNS.

        Returns:
            Dict of repo name -> sorted list of unique accessions found.
            Repos with no matches are omitted. Empty dict if full text is
            unavailable.
        """
        patterns = patterns or ACCESSION_PATTERNS
        xml = self.fetch_full_text_xml(pmcid)
        if not xml:
            logger.warning(f"No full text available for {pmcid}; cannot mine accessions")
            return {}

        found = {}
        for repo, pattern in patterns.items():
            matches = sorted(set(re.findall(pattern, xml)))
            if matches:
                found[repo] = matches

        logger.info(f"Found accessions in {pmcid}: { {k: len(v) for k, v in found.items()} }")
        return found
