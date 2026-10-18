"""
OLS4 (Ontology Lookup Service) client for resolving ontology term accessions.

Used as a fallback when the static maps in ontology_mapper.py do not contain
a match for a given term string.

API: https://www.ebi.ac.uk/ols4/api/v2/entities
  GET ?search=<term>&ontologyId=<id>&exact=false&limit=1
  Response: {"elements": [{"curie": "NCBITaxon:9606", "label": ["Homo sapiens"], ...}]}

Results are cached in-process (module-level dict) to avoid redundant HTTP calls
within a single pipeline run.
"""

import logging
from typing import Dict, Optional, Tuple
import urllib.request
import urllib.parse
import json

logger = logging.getLogger(__name__)

# Module-level in-process cache: (term_lower, ontology_ids_tuple) -> (label, curie) | None
_CACHE: Dict[Tuple[str, tuple], Optional[Tuple[str, str]]] = {}

OLS4_BASE = "https://www.ebi.ac.uk/ols4/api/v2/entities"
_TIMEOUT = 10  # seconds per request


def lookup(
    term: str,
    ontology_ids: list,
    exact: bool = False,
) -> Optional[Tuple[str, str]]:
    """
    Look up a term in OLS4 and return (preferred_label, curie).

    Tries each ontology in *ontology_ids* in order and returns the first hit.
    Returns None if no match is found or the request fails.

    Args:
        term:          Free-text term to search (e.g. "Homo sapiens", "HCD")
        ontology_ids:  List of OLS4 ontology IDs to search, e.g. ["ncbitaxon"]
        exact:         If True, requests exact string match only

    Returns:
        (label, curie) tuple, e.g. ("Homo sapiens", "NCBITaxon:9606"), or None
    """
    if not term or not ontology_ids:
        return None

    cache_key = (term.lower().strip(), tuple(ontology_ids))
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    result = None
    for ont_id in ontology_ids:
        result = _query_ols4(term, ont_id, exact=exact)
        if result:
            break

    _CACHE[cache_key] = result
    return result


def _query_ols4(
    term: str, ontology_id: str, exact: bool = False
) -> Optional[Tuple[str, str]]:
    """Execute a single OLS4 search request."""
    params = {
        "search": term,
        "ontologyId": ontology_id,
        "limit": "1",
    }
    if exact:
        params["exact"] = "true"

    url = OLS4_BASE + "?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        elements = data.get("elements", [])
        if not elements:
            return None

        hit = elements[0]
        curie = hit.get("curie")
        labels = hit.get("label", [])
        label = labels[0] if labels else term

        if curie:
            logger.debug("OLS4 resolved '%s' in %s → %s (%s)", term, ontology_id, label, curie)
            return (label, curie)

    except Exception as exc:
        logger.debug("OLS4 lookup failed for '%s' in %s: %s", term, ontology_id, exc)

    return None
