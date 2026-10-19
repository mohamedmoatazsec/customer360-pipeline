"""API resilience tests use fake HTTP responses, so CI needs no external API."""

import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from customer360.pipeline import PipelineError, SourceError
from customer360.source import fetch_collection


def response(payload: dict):
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def test_fetch_collection_follows_all_pages():
    requests = []

    def opener(request, timeout):
        assert timeout > 0
        requests.append(request)
        query = parse_qs(urlparse(request.full_url).query)
        skip = int(query["skip"][0])
        rows = [{"id": value} for value in range(skip + 1, min(skip + 2, 5) + 1)]
        return response({"users": rows, "total": 5, "skip": skip, "limit": 2})

    assert fetch_collection("users", page_size=2, opener=opener) == [
        {"id": value} for value in range(1, 6)
    ]
    assert [parse_qs(urlparse(request.full_url).query)["skip"] for request in requests] == [
        ["0"],
        ["2"],
        ["4"],
    ]
    assert all(request.get_header("User-agent") for request in requests)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_http_errors_retry_before_success(status):
    attempts = []
    delays = []

    def opener(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise HTTPError(request.full_url, status, "temporary", {}, None)
        return response({"products": [{"id": 101}], "total": 1, "skip": 0, "limit": 50})

    assert fetch_collection("products", opener=opener, sleep=delays.append) == [{"id": 101}]
    assert len(attempts) == 3
    assert len(delays) == 2
    assert all(0 < delay <= 10 for delay in delays)


def test_nonretryable_http_error_fails_without_sleep():
    attempts = []
    delays = []

    def opener(request, timeout):
        attempts.append(request.full_url)
        raise HTTPError(request.full_url, 403, "forbidden", {}, None)

    with pytest.raises(SourceError):
        fetch_collection("carts", opener=opener, sleep=delays.append)
    assert len(attempts) == 1
    assert delays == []


def test_retry_budget_is_bounded_on_network_failure():
    attempts = []
    delays = []

    def opener(request, timeout):
        attempts.append(request.full_url)
        raise URLError("offline")

    with pytest.raises(SourceError):
        fetch_collection("carts", opener=opener, sleep=delays.append, max_attempts=3)
    assert len(attempts) == 3
    assert len(delays) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"carts": [], "total": 1, "skip": 0, "limit": 50},
        {"carts": [{"id": 1}], "total": "1", "skip": 0, "limit": 50},
        {"carts": "wrong shape", "total": 1, "skip": 0, "limit": 50},
        {"wrong_key": [], "total": 0, "skip": 0, "limit": 50},
    ],
)
def test_malformed_or_incomplete_page_cannot_silently_truncate(payload):
    with pytest.raises(PipelineError):
        fetch_collection("carts", opener=lambda request, timeout: response(payload))


def test_invalid_json_fails_as_source_error():
    with pytest.raises(SourceError):
        fetch_collection("users", opener=lambda request, timeout: io.BytesIO(b"not-json"))


def test_changed_source_total_fails_instead_of_mixing_snapshots():
    def opener(request, timeout):
        skip = int(parse_qs(urlparse(request.full_url).query)["skip"][0])
        return response(
            {"users": [{"id": skip + 1}], "total": 2 if skip == 0 else 3, "skip": skip, "limit": 1}
        )

    with pytest.raises(SourceError, match="total changed"):
        fetch_collection("users", page_size=1, opener=opener)


def test_repeated_offset_is_rejected():
    def opener(request, timeout):
        return response({"users": [{"id": 1}], "total": 2, "skip": 0, "limit": 1})

    with pytest.raises(PipelineError, match="offset"):
        fetch_collection("users", page_size=1, opener=opener)
