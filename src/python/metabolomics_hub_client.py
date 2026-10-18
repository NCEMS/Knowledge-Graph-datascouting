"""
MetabolomicsHub stopgap client.

MetabolomicsHub (https://www.metabolomicshub.org/) aggregates and harmonizes
metadata from MetaboLights, Metabolomics Workbench, and GNPS/MassIVE into a
common "MHD" (MetabolomicsHub Data) model, and is the resource the proposal
explicitly names as "Metabolomics Hub."

As of this scouting/implementation pass, MetabolomicsHub does **not** expose
a stable, documented public query API:
  - The public site (https://www.metabolomicshub.org/) is a client-rendered
    Next.js application; its dataset browser at /portal does not call a
    discoverable JSON API from static inspection.
  - Probed candidate API routes all 404: /api/v1/docs, /api/docs,
    /api/v1/openapi.json.
  - The organization's own GitHub (https://github.com/MetabolomicsHub)
    ships per-source *conversion* tooling rather than a query API:
    mhd-model (the shared schema), mtbls-mhd-integration,
    mw-mhd-integration, and gnps-mhd-integration.

Per the data-scouting report's own recommendation ("pin snapshots/schema
versions" — the UI and data model are explicitly not final), this client is
intentionally a thin stopgap: it exposes a raw portal-page fetch plus
documented NotImplementedError stubs for the search/fetch operations we
actually want, so pipeline code has a stable import target to migrate onto
once MetabolomicsHub publishes a versioned API.

Interim strategy: MetabolomicsHub's own harmonization pipeline sources its
content from MetaboLights, Metabolomics Workbench, and GNPS/MassIVE, which
are already covered by metabolights_client.py, metabolomics_workbench_client.py,
and gnps_client.py. Prefer those clients for actual data/metadata retrieval
until this client is filled in.
"""

import logging
from typing import Any, Dict, List, Optional

import requests

from http_utils import get_text_with_retry

logger = logging.getLogger(__name__)


class MetabolomicsHubClient:
    """Stopgap client for the MetabolomicsHub portal (no stable public API yet)"""

    def __init__(self, portal_base: str = "https://www.metabolomicshub.org", timeout: int = 30):
        """
        Initialize MetabolomicsHub client

        Args:
            portal_base: MetabolomicsHub portal base URL
            timeout: Request timeout in seconds
        """
        self.portal_base = portal_base
        self.timeout = timeout
        self.session = requests.Session()

    def fetch_portal_page(self, path: str = "/portal") -> Optional[str]:
        """
        Fetch a raw HTML page from the MetabolomicsHub portal.

        This does not return structured data — it is a stopgap for manual
        inspection/scraping until a documented JSON API exists. Do not build
        pipeline logic that parses this HTML; it is not a stable contract.

        Args:
            path: Portal path to fetch, e.g. "/portal"

        Returns:
            Raw HTML text, or None if the request failed
        """
        url = f"{self.portal_base}{path}"
        logger.info(f"Fetching MetabolomicsHub portal page {path} (stopgap, unstructured)")
        return get_text_with_retry(self.session, url, timeout=self.timeout)

    def search_datasets(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """
        Search MetabolomicsHub-indexed datasets.

        Not yet implemented: MetabolomicsHub has no documented public search
        API (see module docstring). Use metabolights_client.py,
        metabolomics_workbench_client.py, or gnps_client.py directly against
        the underlying source repositories instead.

        Raises:
            NotImplementedError: always, until MetabolomicsHub publishes a
                versioned API (re-check /api/v1/openapi.json and
                https://github.com/MetabolomicsHub periodically)
        """
        raise NotImplementedError(
            "MetabolomicsHub has no documented public search API yet. "
            "Query MetaboLightsClient / MetabolomicsWorkbenchClient / GNPSClient directly instead. "
            "See the module docstring in metabolomics_hub_client.py for details."
        )

    def fetch_dataset(self, accession: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single harmonized (MHD-model) dataset record from MetabolomicsHub.

        Not yet implemented: MetabolomicsHub has no documented public
        per-dataset retrieval API (see module docstring).

        Raises:
            NotImplementedError: always, until MetabolomicsHub publishes a
                versioned API
        """
        raise NotImplementedError(
            "MetabolomicsHub has no documented public per-dataset API yet. "
            "Fetch study details from the originating source repository "
            "(MetaboLights, Metabolomics Workbench, or GNPS/MassIVE) instead. "
            "See the module docstring in metabolomics_hub_client.py for details."
        )
