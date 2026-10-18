"""Mocked unit tests for the MetabolomicsHubClient stopgap."""

import pytest
import responses

from metabolomics_hub_client import MetabolomicsHubClient


@responses.activate
def test_fetch_portal_page():
    responses.add(
        responses.GET, "https://www.metabolomicshub.org/portal",
        body="<html>portal</html>", status=200,
    )
    client = MetabolomicsHubClient()
    html = client.fetch_portal_page()
    assert "portal" in html


def test_search_datasets_not_implemented():
    client = MetabolomicsHubClient()
    with pytest.raises(NotImplementedError):
        client.search_datasets("glucose")


def test_fetch_dataset_not_implemented():
    client = MetabolomicsHubClient()
    with pytest.raises(NotImplementedError):
        client.fetch_dataset("ST000001")
