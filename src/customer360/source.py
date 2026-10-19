"""Bounded, paginated extraction. Source bodies remain in memory until sanitized."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .errors import ContractError, SourceError

MAX_RECORDS = 100_000
MAX_PAGES = 2_000
MAX_RESPONSE_BYTES = 10_000_000
SELECT = {
    "users": "id,email,address",
    "products": "id,title,category,price",
    "carts": "id,products,total,discountedTotal,userId,totalProducts,totalQuantity",
}


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def fetch_collection(
    resource: str,
    *,
    base_url: str = "https://dummyjson.com",
    page_size: int = 50,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] | None = None,
    max_attempts: int = 3,
) -> list[dict[str, Any]]:
    """Fetch a complete collection or fail; partial extracts are never returned.

    The expected total must stay consistent across pages. This detects some source
    changes, but cannot provide transactional snapshot isolation for a public API.
    """
    if resource not in SELECT:
        raise ValueError("Unsupported source collection")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer between 1 and 100")
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 1 <= max_attempts <= 5
    ):
        raise ValueError("max_attempts must be between 1 and 5")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base_url must be an HTTP(S) origin or path without credentials or query")
    request_open = opener or urlopen
    pause = sleep or time.sleep
    records: list[dict[str, Any]] = []
    expected_total: int | None = None
    for _ in range(MAX_PAGES):
        offset = len(records)
        query = urlencode({"limit": page_size, "skip": offset, "select": SELECT[resource]})
        url = f"{base_url.rstrip('/')}/{resource}?{query}"
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "Customer360-Portfolio/1.0"},
        )
        body: bytes | None = None
        for attempt in range(max_attempts):
            try:
                with request_open(request, timeout=15) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                break
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if not retryable or attempt + 1 == max_attempts:
                    raise SourceError(f"{resource}: HTTP {exc.code}; extraction stopped") from None
            except (URLError, TimeoutError, OSError):
                if attempt + 1 == max_attempts:
                    raise SourceError(
                        f"{resource}: request failed after {max_attempts} attempts"
                    ) from None
            pause(min(2**attempt, 8))
        if body is None or len(body) > MAX_RESPONSE_BYTES:
            raise SourceError(f"{resource}: response was empty or exceeded the size limit")
        try:
            payload = json.loads(body, parse_float=Decimal)
        except (ValueError, UnicodeError):
            raise SourceError(f"{resource}: response is not valid JSON") from None
        if not isinstance(payload, dict):
            raise ContractError(f"{resource}: response must be an object")
        page = payload.get(resource)
        total = payload.get("total")
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise ContractError(f"{resource}: expected a list of objects")
        if not _nonnegative_int(total) or total > MAX_RECORDS:
            raise ContractError(f"{resource}: invalid or excessive total")
        if payload.get("skip") != offset or isinstance(payload.get("skip"), bool):
            raise ContractError(f"{resource}: pagination offset does not match the request")
        if not _nonnegative_int(payload.get("limit")):
            raise ContractError(f"{resource}: pagination limit is invalid")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise SourceError(f"{resource}: total changed during extraction; retry the snapshot")
        if len(page) > page_size or len(records) + len(page) > total:
            raise ContractError(f"{resource}: inconsistent page size or total")
        records.extend(page)
        if len(records) == total:
            if not records:
                raise ContractError(f"{resource}: empty snapshots are not published")
            return records
        if not page:
            raise SourceError(f"{resource}: pagination ended before the declared total")
    raise SourceError(f"{resource}: pagination exceeded the configured bound")


def extract_live(base_url: str, page_size: int) -> dict[str, list[dict[str, Any]]]:
    """Extract all three resources before any sanitized snapshot is written."""
    return {
        resource: fetch_collection(resource, base_url=base_url, page_size=page_size)
        for resource in SELECT
    }
