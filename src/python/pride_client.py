"""
PRIDE API Client for fetching project metadata and file listings
"""

import logging
import re
import time
from typing import Dict, List, Optional, Any, Tuple
import requests

logger = logging.getLogger(__name__)

_SEARCH_SUMMARY_REFERENCE_RE = re.compile(r"--pub[Mm]ed:(\d+)--doi:\s*(\S+)")


class PrideClient:
    """Client for PRIDE REST API interactions"""
    
    def __init__(self, base_url: str = "https://www.ebi.ac.uk/pride/ws/archive/v3", timeout: int = 30):
        """
        Initialize PRIDE client
        
        Args:
            base_url: PRIDE API base URL
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json"})
    
    def _get_json(self, url: str, params: Optional[Dict] = None, max_retries: int = 5) -> Optional[Any]:
        """
        GET JSON with retry logic and exponential backoff
        
        Args:
            url: URL to request
            params: Query parameters
            max_retries: Maximum retry attempts
            
        Returns:
            JSON response or None if failed
        """
        backoff = 1.5
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                
                if response.status_code == 429:  # Rate limited
                    retry_after = float(response.headers.get("Retry-After", 3))
                    logger.warning(f"Rate limited. Waiting {retry_after}s before retry...")
                    time.sleep(retry_after)
                    continue
                
                response.raise_for_status()
                return response.json()
                
            except requests.RequestException as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to fetch {url} after {max_retries} attempts: {e}")
                    raise
                
                wait_time = backoff ** attempt
                logger.warning(f"Attempt {attempt + 1} failed. Retrying in {wait_time}s...")
                time.sleep(wait_time)
        
        return None
    
    def fetch_project_details(self, pxd: str) -> Optional[Dict[str, Any]]:
        """
        Fetch project metadata
        
        Args:
            pxd: PRIDE project accession (e.g., "PXD000001")
            
        Returns:
            Project metadata dict or None if failed
        """
        url = f"{self.base_url}/projects/{pxd}"
        logger.info(f"Fetching project details for {pxd}")
        return self._get_json(url)

    def fetch_projects_page(self, page: int, page_size: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch one page of the bulk project search/listing endpoint. Each
        returned project summary already includes the same "references"
        field (self-reported pubmed/doi citation strings) as
        fetch_project_details(), so a full sweep of all public PRIDE
        projects' publications needs only ~total_projects/page_size calls,
        not one call per project.

        Args:
            page: Zero-based page index
            page_size: Results per page

        Returns:
            List of project summary dicts (empty list once pages are exhausted)
        """
        url = f"{self.base_url}/search/projects"
        data = self._get_json(url, params={"pageSize": page_size, "page": page})
        return data or []

    def iter_all_projects(self, page_size: int = 100):
        """
        Generator that yields every public PRIDE project summary by
        paginating fetch_projects_page() until an empty page is returned.

        Args:
            page_size: Results per page

        Yields:
            Project summary dicts, one at a time
        """
        page = 0
        while True:
            projects = self.fetch_projects_page(page, page_size=page_size)
            if not projects:
                break
            for project in projects:
                yield project
            page += 1

    def fetch_project_files(self, pxd: str, page_size: int = 1000) -> List[Dict[str, Any]]:
        """
        Fetch all files for a project (paginated)
        
        Args:
            pxd: PRIDE project accession
            page_size: Results per page
            
        Returns:
            List of file metadata dicts
        """
        files = []
        page = 0
        logger.info(f"Fetching files for {pxd}")
        
        while True:
            url = f"{self.base_url}/projects/{pxd}/files"
            data = self._get_json(url, params={"pageSize": page_size, "page": page})
            
            if not data or not isinstance(data, list) or len(data) == 0:
                break
            
            files.extend(data)
            logger.info(f"  Page {page}: {len(data)} files")
            page += 1
        
        logger.info(f"Total files fetched: {len(files)}")
        return files
    
    def extract_file_download_links(self, pxd: str) -> Optional[Dict[str, Any]]:
        """
        Extract file download links (FTP URLs) from project files
        
        Fetches all files using existing pagination logic and extracts:
        - fileName: Name of the file
        - fileSizeBytes: Size in bytes
        - ftpUrl: FTP protocol URL for download
        
        Args:
            pxd: PRIDE project accession
            
        Returns:
            Dict with files list and metadata, or None if failed:
            {
                "files": [
                    {
                        "fileName": "example.raw",
                        "fileSizeBytes": 1024000,
                        "ftpUrl": "ftp://ftp.pride.ebi.ac.uk/..."
                    },
                    ...
                ],
                "totalCount": 42
            }
            Returns None if fetch fails (caller should set to null)
        """
        try:
            logger.info(f"Extracting file download links for {pxd}")
            files = self.fetch_project_files(pxd)
            
            extracted = []
            for file_obj in files:
                file_info = {
                    "fileName": file_obj.get("fileName"),
                    "fileSizeBytes": file_obj.get("fileSizeBytes"),
                    "ftpUrl": None
                }
                
                # Extract FTP URL from publicFileLocations
                public_locations = file_obj.get("publicFileLocations", [])
                for location in public_locations:
                    if location.get("name") == "FTP Protocol":
                        file_info["ftpUrl"] = location.get("value")
                        break
                
                extracted.append(file_info)
            
            result = {
                "files": extracted,
                "totalCount": len(extracted)
            }
            
            logger.info(f"Extracted {len(extracted)} file links for {pxd}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to extract file download links for {pxd}: {e}")
            return None
    
    def extract_publication_info(self, project_details: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
        """
        Extract DOI and PubMed IDs from project details
        
        Handles cases where:
        - Multiple references exist
        - PubMed IDs are invalid/placeholder (0, None, empty)
        - DOI is available but no PMID
        
        Args:
            project_details: Project metadata dict from fetch_project_details
            
        Returns:
            Tuple of (doi, [list of valid pubmed_ids])
        """
        # Get top-level DOI first
        doi = project_details.get("doi", "")
        
        pubmed_ids = []
        references = project_details.get("references", [])
        
        for ref in references:
            # Extract valid pubmedID (skip 0 and None)
            pmid = ref.get("pubmedID")
            if pmid and pmid != 0 and str(pmid).strip():
                pubmed_ids.append(str(pmid))
            
            # If no valid PMID in references but DOI exists, use it
            if not doi and "doi" in ref:
                doi = ref["doi"]
        
        logger.info(f"Extracted DOI: {doi}, Valid PubMed IDs: {pubmed_ids}")
        
        if not pubmed_ids and doi:
            logger.warning(f"No valid PubMed IDs found, but DOI available: {doi}")
        
        return doi, pubmed_ids

    def extract_publication_info_from_search_summary(self, project_summary: Dict[str, Any]
                                                       ) -> Tuple[Optional[str], List[str]]:
        """
        Extract DOI and PubMed IDs from a project summary returned by the
        bulk search/listing endpoint (fetch_projects_page()/iter_all_projects()).

        That endpoint's "references" field is a list of pre-formatted
        citation strings (e.g. "...Adv Sci (Weinh). 2026 13(51):e76205
        --pubMed:42348392--doi: 10.1002/advs.76205"), not the structured
        list-of-dicts shape fetch_project_details() returns — this method
        parses that string format instead of extract_publication_info()'s
        dict format.

        Args:
            project_summary: One project dict from fetch_projects_page()/
                iter_all_projects()

        Returns:
            Tuple of (doi, [list of valid pubmed_ids]); PubMed ID "0" (PRIDE's
            placeholder for "no PMID yet") is treated as absent, matching
            extract_publication_info()'s handling of the structured format
        """
        doi = None
        pubmed_ids = []

        for ref in project_summary.get("references", []) or []:
            match = _SEARCH_SUMMARY_REFERENCE_RE.search(ref or "")
            if not match:
                continue
            pmid, ref_doi = match.group(1), match.group(2)
            if pmid and pmid != "0":
                pubmed_ids.append(pmid)
            if not doi and ref_doi:
                doi = ref_doi

        return doi, pubmed_ids

