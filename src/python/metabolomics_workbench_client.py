"""
Metabolomics Workbench (NIH National Metabolomics Data Repository) REST API
Client for fetching study metadata, factors, named-metabolite results, and
compound/RefMet cross-reference data.

API reference: https://www.metabolomicsworkbench.org/tools/mw_rest.php
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from http_utils import get_json_with_retry, get_text_with_retry

logger = logging.getLogger(__name__)

_MWTAB_PUBMED_RE = re.compile(r"pubmed/(\d+)", re.IGNORECASE)
_MWTAB_DOI_RE = re.compile(r"doi:\s*(10\.\S+)", re.IGNORECASE)


def _normalize_rows(data: Any) -> List[Dict[str, Any]]:
    """
    Normalize Metabolomics Workbench REST responses to a list of row dicts.

    MW returns an empty list `[]` for "no results", a single flat dict for
    unambiguous single-row lookups, and a dict keyed by stringified integer
    indices ("1", "2", ...) for multi-row results. This collapses all three
    shapes into a plain list.
    """
    if not data:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if all(str(k).isdigit() for k in data.keys()):
            return list(data.values())
        return [data]
    return []


class MetabolomicsWorkbenchClient:
    """Client for Metabolomics Workbench REST API interactions"""

    def __init__(self, base_url: str = "https://www.metabolomicsworkbench.org/rest", timeout: int = 30):
        """
        Initialize Metabolomics Workbench client

        Args:
            base_url: Metabolomics Workbench REST API base URL
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get_json(self, url: str, max_retries: int = 5) -> Optional[Any]:
        """GET JSON with retry logic and exponential backoff"""
        return get_json_with_retry(self.session, url, timeout=self.timeout, max_retries=max_retries)

    def _get_text(self, url: str, max_retries: int = 5) -> Optional[str]:
        """GET raw text with retry logic and exponential backoff"""
        return get_text_with_retry(self.session, url, timeout=self.timeout, max_retries=max_retries)

    def _fetch(self, context: str, input_item: str, input_value: str,
               output_item: str, output_format: str = "json") -> Optional[Any]:
        """
        Low-level wrapper over MW's <context>/<input_item>/<input_value>/<output_item>
        REST URL structure.
        """
        url = f"{self.base_url}/{context}/{input_item}/{input_value}/{output_item}"
        if output_format != "json":
            url = f"{url}/{output_format}"
        return self._get_json(url)

    def fetch_all_study_ids(self) -> List[str]:
        """
        Fetch every public study_id in one call via MW's "ST" wildcard input
        value, which returns the full study_id/project_id/analysis_id index
        rather than a single study (MW has no server-side pagination here).

        Returns:
            Sorted list of unique study_id accessions (e.g. ["ST000001", ...])
        """
        logger.info("Fetching all Metabolomics Workbench study IDs")
        rows = _normalize_rows(self._fetch("study", "study_id", "ST", "available"))
        study_ids = sorted({row["study_id"] for row in rows if row.get("study_id")})
        logger.info(f"Fetched {len(study_ids)} unique Metabolomics Workbench study IDs")
        return study_ids

    def fetch_study_mwtab_text(self, study_id: str) -> Optional[str]:
        """
        Fetch a study's full legacy mwTab text dump, which (unlike the clean
        JSON summary/factors/metabolites endpoints) includes the PROJECT
        block's free-text "PR:PUBLICATIONS" field — the only place MW
        exposes a study's associated publication.

        Args:
            study_id: Metabolomics Workbench study accession

        Returns:
            Raw mwTab text, or None if unavailable
        """
        logger.info(f"Fetching mwTab text dump for {study_id}")
        return self._get_text(f"{self.base_url}/study/study_id/{study_id}/mwtab/txt")

    def extract_publication_info_from_mwtab(self, mwtab_text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract a DOI and PubMed ID from a study's mwTab text dump's
        "PR:PUBLICATIONS" field.

        That field is unstructured free text and is sometimes wrapped across
        multiple "PR:PUBLICATIONS" lines (treated as one citation here by
        joining them), may contain a PubMed link in either plain
        ("pubmed/12345") or HTML-anchor form, and may have an empty "DOI:"
        label with no actual DOI following it — this only accepts a DOI that
        matches the standard "10.xxxx/..." format to avoid mistaking an
        adjacent PubMed URL for a DOI value.

        Args:
            mwtab_text: Raw text from fetch_study_mwtab_text()

        Returns:
            Tuple of (doi, pmid), either or both of which may be None
        """
        lines = [line for line in mwtab_text.splitlines() if line.startswith("PR:PUBLICATIONS")]
        combined = " ".join(line.split("\t", 1)[-1].strip() for line in lines)

        pmid_match = _MWTAB_PUBMED_RE.search(combined)
        doi_match = _MWTAB_DOI_RE.search(combined)

        pmid = pmid_match.group(1) if pmid_match else None
        doi = doi_match.group(1).rstrip(".,;)") if doi_match else None
        return doi, pmid

    def fetch_study_summary(self, study_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single study's summary (title, species, institute, analysis
        type, sample count, license).

        Args:
            study_id: Metabolomics Workbench study accession (e.g., "ST000001")

        Returns:
            Study summary dict, or None if not found
        """
        logger.info(f"Fetching study summary for {study_id}")
        rows = _normalize_rows(self._fetch("study", "study_id", study_id, "summary"))
        if not rows:
            logger.warning(f"No study summary found for {study_id}")
            return None
        return rows[0]

    def fetch_study_factors(self, study_id: str) -> List[Dict[str, Any]]:
        """
        Fetch per-sample factor/treatment assignments for a study.

        Args:
            study_id: Metabolomics Workbench study accession

        Returns:
            List of row dicts (local_sample_id, sample_source, factors, ...)
        """
        logger.info(f"Fetching study factors for {study_id}")
        return _normalize_rows(self._fetch("study", "study_id", study_id, "factors"))

    def fetch_study_metabolites(self, study_id: str) -> List[Dict[str, Any]]:
        """
        Fetch named-metabolite results for a study (metabolite_name,
        refmet_name per analysis).

        Args:
            study_id: Metabolomics Workbench study accession

        Returns:
            List of row dicts
        """
        logger.info(f"Fetching study metabolites for {study_id}")
        return _normalize_rows(self._fetch("study", "study_id", study_id, "metabolites"))

    def fetch_study_analysis(self, study_id: str) -> List[Dict[str, Any]]:
        """
        Fetch analysis/instrumentation metadata (chromatography, MS/NMR
        instrument, ion mode) for a study's analyses.

        Args:
            study_id: Metabolomics Workbench study accession

        Returns:
            List of row dicts, one per analysis_id
        """
        logger.info(f"Fetching study analysis metadata for {study_id}")
        return _normalize_rows(self._fetch("study", "study_id", study_id, "analysis"))

    def fetch_study_data(self, study_id: str) -> List[Dict[str, Any]]:
        """
        Fetch the quantitative result matrix for a study (metabolite x sample
        abundance values, nested under a "DATA" key per row).

        Args:
            study_id: Metabolomics Workbench study accession

        Returns:
            List of row dicts with keys study_id, analysis_id, metabolite_name,
            metabolite_id, refmet_name, units, DATA (dict of sample_id -> value)
        """
        logger.info(f"Fetching study data for {study_id}")
        return _normalize_rows(self._fetch("study", "study_id", study_id, "data"))

    def fetch_compound(self, input_item: str, input_value: str, output: str = "all") -> Optional[Dict[str, Any]]:
        """
        Fetch compound cross-reference data for metabolite-identifier
        normalization (PubChem CID, InChIKey, HMDB/KEGG/ChEBI IDs, etc.),
        independent of a specific study.

        Args:
            input_item: Compound lookup key, e.g. "pubchem_cid", "formula",
                "inchi_key", "regno", "lm_id", "kegg_id"
            input_value: Value to look up, e.g. "311"
            output: Output item(s), e.g. "all", "classification", or a
                comma-separated list of specific fields

        Returns:
            Compound dict, or None if not found
        """
        logger.info(f"Fetching compound {input_item}={input_value}")
        rows = _normalize_rows(self._fetch("compound", input_item, input_value, output))
        if not rows:
            logger.warning(f"No compound found for {input_item}={input_value}")
            return None
        return rows[0]

    def resolve_refmet(self, name: str, exact: bool = True) -> Optional[Dict[str, Any]]:
        """
        Resolve a free-text metabolite name to its standardized RefMet
        nomenclature and lipid classification.

        Args:
            name: Metabolite name to resolve, e.g. "Cholesterol"
            exact: If True, use the "name" exact-match endpoint; if False,
                use the "match" fuzzy-match endpoint

        Returns:
            RefMet dict (refmet_id, refmet_name, formula, exactmass,
            super_class/main_class/sub_class, cross-references), or None
        """
        input_item = "name" if exact else "match"
        logger.info(f"Resolving RefMet for '{name}' (exact={exact})")
        rows = _normalize_rows(self._fetch("refmet", input_item, name, "all"))
        if not rows:
            logger.warning(f"No RefMet match found for '{name}'")
            return None
        return rows[0]
