"""
Shared HTTP helpers for API clients.

Provides a single retry/backoff GET-JSON implementation so individual clients
(pride_client.py, metabolights_client.py, metabolomics_workbench_client.py,
gnps_client.py, etc.) don't each reimplement the same loop.
"""

import logging
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


def get_json_with_retry(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    max_retries: int = 5,
    backoff: float = 1.5,
) -> Optional[Any]:
    """
    GET a URL and parse the response as JSON, with retry/backoff logic.

    Retries on connection errors and HTTP errors, and specifically honors
    429 (rate limited) responses by waiting the server-provided Retry-After
    duration (defaulting to 3 seconds) before retrying.

    Args:
        session: A requests.Session to issue the request with
        url: URL to request
        params: Optional query parameters
        headers: Optional per-request headers, merged over (overriding) any
            conflicting session-level headers for this request only
        timeout: Per-request timeout in seconds
        max_retries: Maximum number of attempts before giving up
        backoff: Exponential backoff base (wait = backoff ** attempt)

    Returns:
        Parsed JSON response (dict/list/etc.), or None if the response body
        is empty. Raises requests.RequestException if all retries fail.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)

            if response.status_code == 429:  # Rate limited
                retry_after = float(response.headers.get("Retry-After", 3))
                logger.warning("Rate limited on %s. Waiting %ss before retry...", url, retry_after)
                time.sleep(retry_after)
                continue

            response.raise_for_status()

            if not response.content:
                return None

            return response.json()

        except requests.RequestException as e:
            last_exc = e
            if attempt == max_retries - 1:
                logger.error("Failed to fetch %s after %d attempts: %s", url, max_retries, e)
                raise

            wait_time = backoff ** attempt
            logger.warning("Attempt %d failed for %s. Retrying in %ss...", attempt + 1, url, wait_time)
            time.sleep(wait_time)

    if last_exc:
        raise last_exc
    return None


def get_text_with_retry(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    max_retries: int = 5,
    backoff: float = 1.5,
) -> Optional[str]:
    """
    GET a URL and return the raw response body as text, with the same
    retry/backoff/429-handling behavior as get_json_with_retry().

    Used for non-JSON payloads such as TSV/mwTab downloads and XML full text.

    Args:
        session: A requests.Session to issue the request with
        url: URL to request
        params: Optional query parameters
        headers: Optional per-request headers, merged over (overriding) any
            conflicting session-level headers for this request only (e.g. a
            session that defaults to "Accept: application/json" needs this
            overridden to fetch an XML/text endpoint)
        timeout: Per-request timeout in seconds
        max_retries: Maximum number of attempts before giving up
        backoff: Exponential backoff base (wait = backoff ** attempt)

    Returns:
        Response body as text, or None if the response body is empty.
        Raises requests.RequestException if all retries fail.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 3))
                logger.warning("Rate limited on %s. Waiting %ss before retry...", url, retry_after)
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            return response.text if response.content else None

        except requests.RequestException as e:
            last_exc = e
            if attempt == max_retries - 1:
                logger.error("Failed to fetch %s after %d attempts: %s", url, max_retries, e)
                raise

            wait_time = backoff ** attempt
            logger.warning("Attempt %d failed for %s. Retrying in %ss...", attempt + 1, url, wait_time)
            time.sleep(wait_time)

    if last_exc:
        raise last_exc
    return None
