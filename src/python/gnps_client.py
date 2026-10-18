"""
GNPS2 (Global Natural Products Social Molecular Networking 2) API Client for
fetching cross-repository metabolomics dataset metadata, file manifests,
ReDU-curated sample metadata, spectral library search, and compound
structure enrichment.

GNPS2 is the practical access layer for MassIVE's metabolomics-tagged
holdings and for the GNPS spectral library ecosystem.

API references (no single consolidated spec; endpoints gathered from
https://github.com/Wang-Bioinformatics-Lab/GNPS2_Documentation):
- https://explorer.gnps2.org/api/datasets            (dataset inventory + files)
- https://fasst.gnps2.org/search                      (spectral library search)
- https://redu.gnps2.org/attribute/...                (ReDU sample metadata)
- https://structure.gnps2.org/...                     (structure conversions)
- https://npclassifier.gnps2.org/classify              (natural-product classification)
"""

import json
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

from http_utils import get_json_with_retry, get_text_with_retry

logger = logging.getLogger(__name__)


class GNPSClient:
    """Client for GNPS2 REST API interactions"""

    def __init__(self,
                 explorer_base: str = "https://explorer.gnps2.org/api",
                 fasst_base: str = "https://fasst.gnps2.org",
                 redu_base: str = "https://redu.gnps2.org",
                 structure_base: str = "https://structure.gnps2.org",
                 npclassifier_base: str = "https://npclassifier.gnps2.org",
                 timeout: int = 60):
        """
        Initialize GNPS2 client

        Args:
            explorer_base: Dataset explorer API base URL
            fasst_base: FASST spectral search API base URL
            redu_base: ReDU sample-metadata API base URL
            structure_base: Chemical structure conversion API base URL
            npclassifier_base: NPClassifier natural-product classification API base URL
            timeout: Request timeout in seconds (spectral searches can be slow)
        """
        self.explorer_base = explorer_base
        self.fasst_base = fasst_base
        self.redu_base = redu_base
        self.structure_base = structure_base
        self.npclassifier_base = npclassifier_base
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get_json(self, url: str, params: Optional[Dict] = None, max_retries: int = 5) -> Optional[Any]:
        """GET JSON with retry logic and exponential backoff"""
        return get_json_with_retry(self.session, url, params=params, timeout=self.timeout, max_retries=max_retries)

    def _get_text(self, url: str, params: Optional[Dict] = None, max_retries: int = 5) -> Optional[str]:
        """GET raw text with retry logic and exponential backoff"""
        return get_text_with_retry(self.session, url, params=params, timeout=self.timeout, max_retries=max_retries)

    def fetch_datasets(self, query: Optional[str] = None, repository: Optional[str] = None,
                        domain: str = "metabolomics") -> List[Dict[str, Any]]:
        """
        Fetch the cross-repository metabolomics dataset inventory (spans
        MassIVE/GNPS, MetaboLights, and Metabolomics Workbench).

        Note: this endpoint is not server-side paginated; it returns the
        full matching result set in one response.

        Args:
            query: Optional free-text search term (keywords, title, species)
            repository: Optional source-repository filter, e.g. "MassIVE",
                "MetaboLights", "Metabolomics Workbench"
            domain: "metabolomics" (default) or "proteomics"

        Returns:
            List of dataset dicts (accession, repository, species,
            instrument, title, keywords, pi, date, url)
        """
        params = {"domain": domain}
        if query:
            params["q"] = query
        if repository:
            params["repository"] = repository

        logger.info(f"Fetching GNPS2 dataset inventory (domain={domain}, query={query}, repository={repository})")
        data = self._get_json(f"{self.explorer_base}/datasets", params=params)
        if not data:
            return []

        datasets = data.get("datasets", [])
        logger.info(f"Fetched {len(datasets)} of {data.get('count')} datasets (truncated={data.get('truncated')})")
        return datasets

    def fetch_dataset_files(self, accession: str) -> List[Dict[str, Any]]:
        """
        Fetch the file manifest for a single dataset.

        Args:
            accession: Dataset accession, e.g. "MSV000086989"

        Returns:
            List of file dicts (filename, collection, size_mb, ms2, Vendor, Model)
        """
        logger.info(f"Fetching file manifest for {accession}")
        data = self._get_json(f"{self.explorer_base}/datasets/{accession}/files")
        return data or []

    def search_by_usi(self, usi: str, library: str = "gnpslibrary", **kwargs) -> Optional[Dict[str, Any]]:
        """
        Search a spectral library (FASST) for matches to a spectrum
        identified by USI.

        Args:
            usi: Universal Spectrum Identifier,
                e.g. "mzspec:GNPS:GNPS-LIBRARY:accession:CCMSLIB00000001547"
            library: Pre-built library index (see the FASST GUI for options)
            **kwargs: Optional search parameters: analog ("Yes"/"No"),
                cache ("Yes"/"No"), lower_delta, upper_delta,
                pm_tolerance (Da), fragment_tolerance (Da), cosine_threshold

        Returns:
            Dict with "results", "grouped_by_dataset", etc., or None if failed
        """
        params = {"usi": usi, "library": library, **kwargs}
        logger.info(f"Searching spectral library '{library}' by USI {usi}")
        return self._get_json(f"{self.fasst_base}/search", params=params)

    def search_by_peaks(self, precursor_mz: float, peaks: List[List[float]], charge: int = 1,
                         library: str = "gnpslibrary", **kwargs) -> Optional[Dict[str, Any]]:
        """
        Search a spectral library (FASST) for matches to an explicit peak list.

        Args:
            precursor_mz: Precursor m/z of the query spectrum
            peaks: List of [mz, intensity] pairs
            charge: Precursor charge
            library: Pre-built library index
            **kwargs: Optional search parameters (see search_by_usi)

        Returns:
            Dict with "results", "grouped_by_dataset", etc., or None if failed
        """
        query_spectrum = json.dumps({
            "n_peaks": len(peaks),
            "peaks": peaks,
            "precursor_charge": charge,
            "precursor_mz": precursor_mz,
        })
        params = {
            "usi": "None",
            "precursor_mz": precursor_mz,
            "charge": charge,
            "library": library,
            "query_spectrum": query_spectrum,
            **kwargs,
        }
        logger.info(f"Searching spectral library '{library}' by peak list ({len(peaks)} peaks)")
        return self._get_json(f"{self.fasst_base}/search", params=params)

    def fetch_attribute_terms(self, attribute: str, filters: Optional[list] = None) -> List[Dict[str, Any]]:
        """
        Fetch all known ReDU values for a given sample-metadata attribute
        (e.g. the controlled-vocabulary terms available for NCBITaxonomy,
        SampleType, UBERONBodyPartName, DOIDCommonName, HealthStatus).

        Args:
            attribute: ReDU attribute name, e.g. "NCBITaxonomy", "SampleType"
            filters: Optional ReDU filter list (passed through as JSON); an
                empty list is used by default

        Returns:
            List of dicts: attributename, attributeterm, ontologyterm, countfiles
        """
        filters_json = _encode_filters(filters)
        url = f"{self.redu_base}/attribute/{urllib.parse.quote(attribute, safe='')}/attributeterms"
        logger.info(f"Fetching ReDU attribute terms for '{attribute}'")
        data = self._get_json(url, params={"filters": filters_json})
        return data or []

    def fetch_sample_metadata(self, attribute: str, term: str,
                               filters: Optional[list] = None) -> List[Dict[str, Any]]:
        """
        Fetch ReDU-curated sample metadata for all files matching a given
        attribute/term pair. Backfills organism/tissue/sample-type/health
        fields that the bare dataset-file manifest lacks.

        Args:
            attribute: ReDU attribute name, e.g. "NCBITaxonomy"
            term: Attribute value, e.g. "9606|Homo sapiens" (NCBITaxonomy
                terms are "<taxid>|<name>"; see fetch_attribute_terms() for
                the exact term strings available for other attributes)
            filters: Optional ReDU filter list; an empty list is used by default

        Returns:
            List of per-file sample-metadata dicts (ATTRIBUTE_DatasetAccession,
            filename, USI, NCBITaxonomy, SampleType, UBERONBodyPartName, ...)
        """
        filters_json = _encode_filters(filters)
        url = (f"{self.redu_base}/attribute/{urllib.parse.quote(attribute, safe='')}"
               f"/attributeterm/{urllib.parse.quote(term, safe='')}/files")
        logger.info(f"Fetching ReDU sample metadata for {attribute}={term}")
        data = self._get_json(url, params={"filters": filters_json})
        return data or []

    def convert_structure(self, kind: str, smiles: Optional[str] = None,
                           inchi: Optional[str] = None) -> Optional[str]:
        """
        Convert/derive a chemical structure representation.

        Args:
            kind: One of "convert" (full resolution), "inchi", "inchikey",
                "mol", "formula", "structuremass"
            smiles: SMILES string input (mutually exclusive with inchi for
                most kinds; "convert" accepts smiles only)
            inchi: InChI string input

        Returns:
            Plain-text result (e.g. an InChIKey or molecular formula), or
            None if neither smiles nor inchi is provided, or the request fails
        """
        if not smiles and not inchi:
            logger.error("convert_structure requires smiles or inchi")
            return None

        params = {}
        if smiles:
            params["smiles"] = smiles
        if inchi:
            params["inchi"] = inchi

        logger.info(f"Converting structure (kind={kind})")
        return self._get_text(f"{self.structure_base}/{kind}", params=params)

    def classify_structure_np(self, smiles: str) -> Optional[Dict[str, Any]]:
        """
        Classify a natural-product structure using NPClassifier.

        Args:
            smiles: SMILES string

        Returns:
            Dict with class_results, superclass_results, pathway_results,
            isglycoside; or None if failed
        """
        logger.info("Classifying structure with NPClassifier")
        return self._get_json(f"{self.npclassifier_base}/classify", params={"smiles": smiles})


def _encode_filters(filters: Optional[list]) -> str:
    """Encode a ReDU filter list as the JSON string the API expects (default: empty list)"""
    return json.dumps(filters if filters is not None else [])
