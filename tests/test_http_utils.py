"""Tests for the shared HTTP retry helper used by all API clients."""

import pytest
import requests
import responses

from http_utils import get_json_with_retry, get_text_with_retry

URL = "https://example.org/data"


@responses.activate
def test_get_json_with_retry_happy_path():
    responses.add(responses.GET, URL, json={"ok": True}, status=200)
    session = requests.Session()
    result = get_json_with_retry(session, URL)
    assert result == {"ok": True}


@responses.activate
def test_get_json_with_retry_retries_on_429():
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "0"})
    responses.add(responses.GET, URL, json={"ok": True}, status=200)
    session = requests.Session()
    result = get_json_with_retry(session, URL, max_retries=3)
    assert result == {"ok": True}
    assert len(responses.calls) == 2


@responses.activate
def test_get_json_with_retry_raises_after_max_retries():
    responses.add(responses.GET, URL, status=500)
    session = requests.Session()
    with pytest.raises(requests.RequestException):
        get_json_with_retry(session, URL, max_retries=1)


@responses.activate
def test_get_json_with_retry_empty_body_returns_none():
    responses.add(responses.GET, URL, body="", status=200)
    session = requests.Session()
    result = get_json_with_retry(session, URL)
    assert result is None


@responses.activate
def test_get_text_with_retry_happy_path():
    responses.add(responses.GET, URL, body="hello", status=200)
    session = requests.Session()
    result = get_text_with_retry(session, URL)
    assert result == "hello"


@responses.activate
def test_get_text_with_retry_raises_after_max_retries():
    responses.add(responses.GET, URL, status=500)
    session = requests.Session()
    with pytest.raises(requests.RequestException):
        get_text_with_retry(session, URL, max_retries=1)
