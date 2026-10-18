"""
MetaboLights API Client for fetching study metadata, file listings, and
metabolite-annotation-file (MAF) tables.

MetaboLights (https://www.ebi.ac.uk/metabolights/) is EMBL-EBI's primary
metabolomics study/raw-data repository and uses an ISA-Tab metadata model.

API reference: https://www.ebi.ac.uk/metabolights/ws/api/spec.html
"""

import csv
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

from http_utils import get_json_with_retry, get_text_with_retry

logger = logging.getLogger(__name__)


class MetaboLightsClient:
    """Client for MetaboLights REST API interactions"""

    def __init__(self, base_url: str = "https://www.ebi.ac.uk/metabolights/ws", timeout: int = 30):
        """
        Initialize MetaboLights client

        Args:
            base_url: MetaboLights API base URL
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get_json(self, url: str, params: Optional[Dict] = None, max_retries: int = 5) -> Optional[Any]:
        """GET JSON with retry logic and exponential backoff"""
        return get_json_with_retry(self.session, url, params=params, timeout=self.timeout, max_retries=max_retries)

    def _get_text(self, url: str, params: Optional[Dict] = None, max_retries: int = 5) -> Optional[str]:
        """GET raw text with retry logic and exponential backoff"""
        return get_text_with_retry(self.session, url, params=params, timeout=self.timeout, max_retries=max_retries)

    def fetch_study_details(self, mtbls_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch study metadata (status, ISA investigation, license, FTP/HTTP paths)

        Args:
            mtbls_id: MetaboLights study accession (e.g., "MTBLS1")

        Returns:
            Study metadata dict with top-level keys "mtblsStudy" and
            "isaInvestigation", or None if failed
        """
        url = f"{self.base_url}/studies/{mtbls_id}"
        logger.info(f"Fetching study details for {mtbls_id}")
        return self._get_json(url)

    def fetch_all_study_accessions(self) -> List[str]:
        """
        Fetch the full list of public MetaboLights study accessions in one
        call (MetaboLights has no server-side pagination for this endpoint —
        it returns the entire list at once).

        Returns:
            List of study accessions (e.g. ["MTBLS1", "MTBLS2", ...])
        """
        logger.info("Fetching all public MetaboLights study accessions")
        data = self._get_json(f"{self.base_url}/studies")
        accessions = (data or {}).get("content", [])
        logger.info(f"Fetched {len(accessions)} MetaboLights study accessions")
        return accessions

    def fetch_study_files(self, mtbls_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the study's file manifest (investigation/sample/assay/MAF files)

        Args:
            mtbls_id: MetaboLights study accession

        Returns:
            Dict with "study"/"latest"/"private" file lists, each entry
            containing "file", "type", "status", "createdAt"; or None if failed
        """
        url = f"{self.base_url}/studies/{mtbls_id}/files"
        logger.info(f"Fetching file manifest for {mtbls_id}")
        return self._get_json(url)

    def fetch_maf_table(self, mtbls_id: str, maf_filename: str,
                         study_details: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Download and parse a metabolite annotation file (MAF) TSV for a study.

        MAF files contain compound identifiers (database_identifier, chemical
        formula, SMILES, InChI), organism/taxid, annotation confidence
        (reliability), and per-sample abundance columns.

        Args:
            mtbls_id: MetaboLights study accession
            maf_filename: MAF file name as listed in fetch_study_files()
                (type == "metadata_maf"), e.g.
                "m_MTBLS1_metabolite_profiling_NMR_spectroscopy_v2_maf.tsv"
            study_details: Optional pre-fetched result of fetch_study_details()
                to avoid a redundant request

        Returns:
            List of row dicts keyed by MAF column header (fixed compound
            columns plus one key per sample abundance column). Empty list if
            the file could not be fetched or parsed.
        """
        if study_details is None:
            study_details = self.fetch_study_details(mtbls_id)

        if not study_details:
            logger.error(f"Cannot fetch MAF table for {mtbls_id}: study details unavailable")
            return []

        http_base = study_details.get("mtblsStudy", {}).get("studyHttpUrl")
        if not http_base:
            logger.error(f"No studyHttpUrl found for {mtbls_id}")
            return []

        url = f"{http_base}/{maf_filename}"
        logger.info(f"Downloading MAF table {maf_filename} for {mtbls_id}")

        try:
            text = self._get_text(url)
        except requests.RequestException as e:
            logger.error(f"Failed to download MAF table {maf_filename} for {mtbls_id}: {e}")
            return []

        if not text:
            logger.warning(f"MAF table {maf_filename} for {mtbls_id} is empty")
            return []

        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        rows = list(reader)
        logger.info(f"Parsed {len(rows)} MAF rows for {mtbls_id}")
        return rows

    def extract_file_download_links(self, mtbls_id: str,
                                     study_details: Optional[Dict[str, Any]] = None,
                                     study_files: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Build download links for a study's files by combining the study's
        FTP/HTTP base paths with its file manifest.

        Args:
            mtbls_id: MetaboLights study accession
            study_details: Optional pre-fetched fetch_study_details() result
            study_files: Optional pre-fetched fetch_study_files() result

        Returns:
            Dict with files list and metadata, mirroring pride_client's shape:
            {
                "files": [
                    {"fileName": "s_MTBLS1.txt", "fileSizeBytes": None,
                     "ftpUrl": "ftp://.../s_MTBLS1.txt",
                     "httpUrl": "http://.../s_MTBLS1.txt", "type": "metadata_sample"},
                    ...
                ],
                "totalCount": 4
            }
            Returns None if fetch fails.
        """
        try:
            if study_details is None:
                study_details = self.fetch_study_details(mtbls_id)
            if study_files is None:
                study_files = self.fetch_study_files(mtbls_id)

            if not study_details or not study_files:
                logger.error(f"Cannot extract file links for {mtbls_id}: missing study details or files")
                return None

            mtbls_study = study_details.get("mtblsStudy", {})
            ftp_base = mtbls_study.get("studyFtpUrl")
            http_base = mtbls_study.get("studyHttpUrl")

            entries = []
            for bucket in ("study", "latest", "private"):
                entries.extend(study_files.get(bucket, []))

            extracted = []
            for entry in entries:
                file_name = entry.get("file")
                extracted.append({
                    "fileName": file_name,
                    "fileSizeBytes": None,
                    "ftpUrl": f"{ftp_base}/{file_name}" if ftp_base and file_name else None,
                    "httpUrl": f"{http_base}/{file_name}" if http_base and file_name else None,
                    "type": entry.get("type"),
                })

            result = {"files": extracted, "totalCount": len(extracted)}
            logger.info(f"Extracted {len(extracted)} file links for {mtbls_id}")
            return result

        except Exception as e:
            logger.error(f"Failed to extract file download links for {mtbls_id}: {e}")
            return None

    def extract_publication_info(self, study_details: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
        """
        Extract DOI and PubMed IDs from a study's ISA investigation publications.

        Args:
            study_details: Study metadata dict from fetch_study_details

        Returns:
            Tuple of (doi, [list of valid pubmed_ids])
        """
        doi = None
        pubmed_ids = []

        studies = study_details.get("isaInvestigation", {}).get("studies", [])
        for study in studies:
            for pub in study.get("publications", []):
                pmid = pub.get("pubMedID")
                if pmid and str(pmid).strip():
                    pubmed_ids.append(str(pmid))

                if not doi and pub.get("doi"):
                    doi = pub["doi"]

        logger.info(f"Extracted DOI: {doi}, Valid PubMed IDs: {pubmed_ids}")
        return doi, pubmed_ids
