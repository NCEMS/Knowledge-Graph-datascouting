"""
NCBI Entrez API Client

Fetches publication data from PubMed and PubMed Central using the NCBI Entrez API.
Requires NCBI_EMAIL and NCBI_API_KEY environment variables or manual configuration.

Reference: https://www.ncbi.nlm.nih.gov/books/NBK25497/
"""

import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


class NCBIClient:
    """
    Client for fetching publication data from NCBI Entrez API.
    
    Handles PubMed/PubMed Central queries with proper rate limiting and error handling.
    Uses environment variables NCBI_EMAIL and NCBI_API_KEY for authentication.
    """
    
    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    MAX_RETRIES = 5
    RETRY_BACKOFF = 1.5
    
    def __init__(self, email: Optional[str] = None, api_key: Optional[str] = None, timeout: int = 10):
        """
        Initialize NCBI API client.
        
        Args:
            email: NCBI email (defaults to NCBI_EMAIL env var)
            api_key: NCBI API key (defaults to NCBI_API_KEY env var)
            timeout: Request timeout in seconds (default: 10)
            
        Raises:
            ValueError: If email is not provided
        """
        self.email = email or os.getenv("NCBI_EMAIL")
        self.api_key = api_key or os.getenv("NCBI_API_KEY")
        self.timeout = timeout
        
        if not self.email:
            raise ValueError(
                "NCBI email required. Set NCBI_EMAIL environment variable "
                "or pass email parameter."
            )
        
        logger.info(f"Initialized NCBIClient with email: {self.email}")
        logger.info(f"API key configured: {bool(self.api_key)}")
    
    def _get_params(self) -> Dict[str, str]:
        """Get common query parameters for all NCBI requests."""
        params = {"email": self.email}
        if self.api_key:
            params["api_key"] = self.api_key
        return params
    
    def _get_json(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict]:
        """
        Make HTTP GET request to NCBI endpoint with retry logic.
        
        Args:
            endpoint: NCBI API endpoint (e.g., 'esummary', 'efetch')
            params: Query parameters
            
        Returns:
            Parsed JSON response or None on failure
        """
        url = f"{self.BASE_URL}/{endpoint}.json"
        
        for attempt in range(self.MAX_RETRIES):
            try:
                logger.debug(f"Request to {endpoint} (attempt {attempt + 1}/{self.MAX_RETRIES})")
                
                response = requests.get(
                    url,
                    params={**params, **self._get_params()},
                    timeout=self.timeout
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 1))
                    logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue
                
                response.raise_for_status()
                data = response.json()
                
                # Check for NCBI errors in response
                if "error" in data:
                    logger.error(f"NCBI API error: {data['error']}")
                    return None
                
                return data
                
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on attempt {attempt + 1}/{self.MAX_RETRIES}")
                if attempt < self.MAX_RETRIES - 1:
                    backoff = 2 ** attempt * self.RETRY_BACKOFF
                    time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    backoff = 2 ** attempt * self.RETRY_BACKOFF
                    time.sleep(backoff)
        
        logger.error(f"Failed to fetch {endpoint} after {self.MAX_RETRIES} attempts")
        return None
    
    def _get_xml(self, endpoint: str, params: Dict[str, Any]) -> Optional[str]:
        """
        Make HTTP GET request returning XML.
        
        Args:
            endpoint: NCBI API endpoint
            params: Query parameters
            
        Returns:
            XML response string or None on failure
        """
        url = f"{self.BASE_URL}/{endpoint}"
        
        for attempt in range(self.MAX_RETRIES):
            try:
                logger.debug(f"XML request to {endpoint} (attempt {attempt + 1}/{self.MAX_RETRIES})")
                
                response = requests.get(
                    url,
                    params={**params, **self._get_params()},
                    timeout=self.timeout
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 1))
                    logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue
                
                response.raise_for_status()
                return response.text
                
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on attempt {attempt + 1}/{self.MAX_RETRIES}")
                if attempt < self.MAX_RETRIES - 1:
                    backoff = 2 ** attempt * self.RETRY_BACKOFF
                    time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    backoff = 2 ** attempt * self.RETRY_BACKOFF
                    time.sleep(backoff)
        
        logger.error(f"Failed to fetch XML from {endpoint} after {self.MAX_RETRIES} attempts")
        return None
    
    def fetch_pubmed_summary(self, pmid: str) -> Optional[Dict[str, Any]]:
        """
        Fetch publication summary from PubMed.
        
        Args:
            pmid: PubMed ID (string or integer)
            
        Returns:
            Dictionary with publication metadata or None on failure
            
        Example:
            >>> client = NCBIClient(email="your@email.com")
            >>> summary = client.fetch_pubmed_summary("12345678")
            >>> print(summary['title'])
        """
        pmid = str(pmid)
        logger.info(f"Fetching PubMed summary for PMID: {pmid}")
        
        xml_data = self._get_xml("esummary", {
            "db": "pubmed",
            "id": pmid
        })
        
        if not xml_data:
            logger.error(f"No results for PMID {pmid}")
            return None
        
        try:
            root = ET.fromstring(xml_data)
            
            # Parse the DocSum (document summary)
            doc_sum = root.find(".//DocSum")
            if doc_sum is None:
                logger.error(f"No DocSum found for PMID {pmid}")
                return None
            
            summary = {
                "pmid": pmid,
                "title": "",
                "abstract": "",
                "authors": [],
                "journal": "",
                "pub_date": "",
                "pmc_id": None,
                "doi": None,
            }
            
            # Extract items from DocSum
            for item in doc_sum.findall("Item"):
                name = item.get("Name", "")
                
                if name == "Title":
                    summary["title"] = item.text or ""
                elif name == "Abstract":
                    summary["abstract"] = item.text or ""
                elif name == "Journal":
                    summary["journal"] = item.text or ""
                elif name == "PubDate":
                    summary["pub_date"] = item.text or ""
                elif name == "DOI":
                    summary["doi"] = item.text or ""
                elif name == "ArtId" and item.get("ArtIdType") == "pmc":
                    summary["pmc_id"] = item.text or None
                elif name == "AuthorList":
                    authors = []
                    for author in item.findall("Author"):
                        author_name = author.get("Name")
                        if author_name:
                            authors.append(author_name)
                    summary["authors"] = authors
            
            logger.info(f"Successfully fetched summary for PMID {pmid}")
            return summary
            
        except ET.ParseError as e:
            logger.error(f"Error parsing XML: {e}")
            return None
    
    def pmid_to_pmcid(self, pmid: str) -> Optional[str]:
        """
        Convert PubMed ID to PubMed Central ID.
        
        Args:
            pmid: PubMed ID
            
        Returns:
            PMC ID (without 'PMC' prefix) or None if not found
            
        Example:
            >>> pmcid = client.pmid_to_pmcid("12345678")
            >>> print(f"PMC{pmcid}")  # PMCXXXXXXX
        """
        pmid = str(pmid)
        logger.info(f"Converting PMID {pmid} to PMCID")
        
        summary = self.fetch_pubmed_summary(pmid)
        if summary and summary.get("pmc_id"):
            pmcid = summary["pmc_id"].replace("PMC", "")
            logger.info(f"PMID {pmid} → PMCID {pmcid}")
            return pmcid
        
        logger.warning(f"Could not find PMCID for PMID {pmid}")
        return None
    
    def fetch_pubmed_full_text(self, pmid: str) -> Optional[Dict[str, Any]]:
        """
        Fetch full text from PubMed (via XML).
        
        Note: Full text availability depends on article permissions.
        Use PMCClient for more reliable full-text access.
        
        Args:
            pmid: PubMed ID
            
        Returns:
            Dictionary with abstract and available text sections or None
        """
        pmid = str(pmid)
        logger.info(f"Fetching full text for PMID: {pmid}")
        
        # First get the summary
        summary = self.fetch_pubmed_summary(pmid)
        if not summary:
            return None
        
        # Try to fetch via XML format
        xml_data = self._get_xml("efetch", {
            "db": "pubmed",
            "id": pmid,
            "rettype": "xml",
            "retmode": "xml"
        })
        
        if not xml_data:
            logger.warning(f"Could not fetch XML for PMID {pmid}")
            return {
                "pmid": pmid,
                "title": summary.get("title", ""),
                "abstract": summary.get("abstract", ""),
                "sections": {}
            }
        
        # Parse XML
        try:
            root = ET.fromstring(xml_data)
            sections = self._parse_pubmed_xml(root)
            
            return {
                "pmid": pmid,
                "title": summary.get("title", ""),
                "abstract": summary.get("abstract", ""),
                "authors": summary.get("authors", []),
                "journal": summary.get("journal", ""),
                "pub_date": summary.get("pub_date", ""),
                "pmc_id": summary.get("pmc_id"),
                "doi": summary.get("doi"),
                "sections": sections
            }
        except ET.ParseError as e:
            logger.error(f"Error parsing XML: {e}")
            return summary
    
    def _parse_pubmed_xml(self, root: ET.Element) -> Dict[str, str]:
        """
        Parse PubMed XML to extract text sections.
        
        Args:
            root: ElementTree root
            
        Returns:
            Dictionary of text sections
        """
        sections = {}
        
        # Try to extract different sections
        for article in root.findall(".//Article"):
            # Abstract
            abstract = article.find("Abstract")
            if abstract is not None:
                abstract_text = self._extract_text_from_element(abstract)
                if abstract_text:
                    sections["abstract"] = abstract_text
            
            # Body (introduction, methods, results, discussion)
            body = article.find("Body")
            if body is not None:
                for sec in body.findall(".//Sec"):
                    title_elem = sec.find("Title")
                    if title_elem is not None:
                        title = title_elem.text or ""
                        text = self._extract_text_from_element(sec)
                        if title and text:
                            sections[title.lower()] = text
        
        return sections
    
    def _extract_text_from_element(self, element: ET.Element) -> str:
        """Extract all text from an XML element."""
        text_parts = []
        
        if element.text:
            text_parts.append(element.text)
        
        for child in element:
            if child.tail:
                text_parts.append(child.tail)
            # Recursively get text from children
            text_parts.append(self._extract_text_from_element(child))
        
        return " ".join(text_parts).strip()
    
    def search_pubmed(self, query: str, retmax: int = 10) -> Optional[List[str]]:
        """
        Search PubMed for articles matching query.
        
        Args:
            query: Search query string
            retmax: Maximum number of results to return (default: 10, max: 100000)
            
        Returns:
            List of PMIDs matching the query or None on failure
            
        Example:
            >>> pmids = client.search_pubmed("cancer treatment 2023", retmax=5)
            >>> for pmid in pmids:
            ...     summary = client.fetch_pubmed_summary(pmid)
        """
        logger.info(f"Searching PubMed: {query}")
        
        xml_data = self._get_xml("esearch", {
            "db": "pubmed",
            "term": query,
            "retmax": min(retmax, 100000),
            "usehistory": "y"
        })
        
        if not xml_data:
            logger.error("No search results")
            return None
        
        try:
            root = ET.fromstring(xml_data)
            pmids = []
            
            for id_elem in root.findall(".//Id"):
                if id_elem.text:
                    pmids.append(id_elem.text)
            
            logger.info(f"Found {len(pmids)} articles for query: {query}")
            return pmids if pmids else None
        except ET.ParseError as e:
            logger.error(f"Error parsing XML: {e}")
            return None
    
    def search_by_doi(self, doi: str) -> Optional[str]:
        """
        Search PubMed for an article by DOI and return its PMID.
        
        Uses NCBI's esearch with DOI-formatted query.
        
        Args:
            doi: Digital Object Identifier (e.g., "10.1016/J.MOLP.2023.03.013")
            
        Returns:
            PMID of the first matching article or None if not found
            
        Example:
            >>> pmid = client.search_by_doi("10.1016/J.MOLP.2023.03.013")
            >>> if pmid:
            ...     summary = client.fetch_pubmed_summary(pmid)
        """
        if not doi:
            logger.warning("DOI is empty or None")
            return None
        
        logger.info(f"Searching PubMed for DOI: {doi}")
        
        # Format: NCBI can search by DOI using "doi:" prefix
        query = f"{doi}[DOI]"
        
        pmids = self.search_pubmed(query, retmax=5)
        
        if pmids and len(pmids) > 0:
            pmid = pmids[0]
            logger.info(f"Found PMID {pmid} for DOI {doi}")
            return pmid
        
        logger.warning(f"No PMID found for DOI: {doi}")
        return None
    
    def fetch_publication_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        Fetch complete publication data from PubMed using a DOI.
        
        Searches PubMed for an article by DOI, retrieves its PMID, and fetches full text/metadata.
        
        Args:
            doi: Digital Object Identifier (e.g., "10.1016/J.MOLP.2023.03.013")
            
        Returns:
            Dictionary with complete publication data (title, abstract, authors, sections, etc.)
            or None if article not found or fetch fails
            
        Example:
            >>> pub_data = client.fetch_publication_by_doi("10.1016/J.MOLP.2023.03.013")
            >>> if pub_data:
            ...     print(f"Title: {pub_data['title']}")
            ...     print(f"Abstract: {pub_data['abstract']}")
        """
        if not doi:
            logger.warning("DOI is empty or None")
            return None
        
        logger.info(f"Fetching publication data from DOI: {doi}")
        
        # Step 1: Search for PMID by DOI
        pmid = self.search_by_doi(doi)
        if not pmid:
            logger.warning(f"Could not find PMID for DOI: {doi}")
            return None
        
        # Step 2: Fetch full publication text/metadata
        logger.info(f"Fetching full publication data for PMID: {pmid}")
        publication_data = self.fetch_pubmed_full_text(pmid)
        
        if publication_data:
            logger.info(f"Successfully retrieved publication data for DOI {doi}")
        else:
            logger.warning(f"Failed to retrieve publication data for PMID {pmid}")
        
        return publication_data
    
    def get_publication_metadata(self, pmid: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive metadata for a publication.
        
        Combines multiple NCBI API calls to get complete information.
        
        Args:
            pmid: PubMed ID
            
        Returns:
            Dictionary with complete publication metadata
            
        Example:
            >>> metadata = client.get_publication_metadata("12345678")
            >>> print(f"{metadata['title']} by {metadata['authors'][0]}")
        """
        logger.info(f"Fetching comprehensive metadata for PMID {pmid}")
        
        summary = self.fetch_pubmed_summary(pmid)
        if not summary:
            return None
        
        metadata = {
            "pmid": pmid,
            "title": summary.get("title", ""),
            "abstract": summary.get("abstract", ""),
            "authors": summary.get("authors", []),
            "journal": summary.get("journal", ""),
            "publication_date": summary.get("pub_date", ""),
            "pmc_id": summary.get("pmc_id"),
            "doi": summary.get("doi"),
            "fetched_at": datetime.now().isoformat(),
            "summary": summary
        }
        
        logger.info(f"Retrieved metadata: {metadata['title'][:50]}...")
        return metadata
