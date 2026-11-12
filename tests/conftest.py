"""Shared fixtures: a mocked beehiiv API so that no test makes a real request."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import pytest
import responses

if TYPE_CHECKING:
    from collections.abc import Iterator

    from requests import PreparedRequest

API = "https://api.beehiiv.com/v2"
PUBLICATIONS = [{"id": "pub_1", "name": "One"}, {"id": "pub_2", "name": "Two"}]


def page_response(data: list[dict[str, Any]], page: int = 1, total_pages: int = 1) -> str:
    """Build a page-paginated response body."""
    return json.dumps(
        {
            "data": data,
            "page": page,
            "limit": 100,
            "total_results": len(data),
            "total_pages": total_pages,
        }
    )


# One record per endpoint, served when a test has not registered its own handler
SAMPLES: dict[str, dict[str, Any]] = {
    "posts": {"id": "post_1", "title": "Hello", "created": 1700000000, "stats": {}},
    "subscriptions": {"id": "sub_1", "email": "reader@example.com", "created": 1700000000},
    "ad_network/reports": {
        "publication_id": "pub_1",
        "post_id": "post_1",
        "ad_link_id": "link_1",
        "revenue": "$1.00",
    },
    "engagements": {"date": "2026-01-01", "total_opens": 1},
    "authors": {"id": "user_1", "name": "Author"},
    "automations": {"id": "aut_1", "name": "Welcome"},
    "custom_fields": {"id": "field_1", "kind": "string", "display": "Name"},
    "newsletter_lists": {"id": "nl_list_1", "name": "List"},
    "polls": {"id": "poll_1", "name": "Poll"},
    "post_templates": {"id": "post_template_1", "name": "Template"},
    "referral_program": {"id": "mile_1", "num_referrals": 3},
    "segments": {"id": "seg_1", "name": "Segment"},
    "tiers": {"id": "tier_1", "name": "Premium"},
}


class MockApi:
    """Routes mocked requests by path.

    Unhandled paths return a sample record, or an empty list when `serve_samples` is off.
    """

    def __init__(self, mock: responses.RequestsMock) -> None:
        """Register the catch-all callback."""
        self.handlers: dict[str, Any] = {}
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        self.serve_samples = True
        mock.add_callback(responses.GET, re.compile(rf"{API}/.*"), callback=self._dispatch)

    def reset(self, *, serve_samples: bool) -> None:
        """Forget registered handlers and recorded requests."""
        self.handlers.clear()
        self.requests.clear()
        self.serve_samples = serve_samples

    def on(self, path: str, handler: Any) -> None:  # noqa: ANN401
        """Handle requests to `path` (relative to the API root) with `handler`."""
        self.handlers[path] = handler

    def calls(self, path: str) -> list[dict[str, list[str]]]:
        """Return the query parameters of each request made to `path`."""
        return [query for called, query in self.requests if called == path]

    def _dispatch(self, request: PreparedRequest) -> tuple[int, dict[str, str], str]:
        parsed = urlparse(request.url)
        path = str(parsed.path).removeprefix("/v2")
        query = parse_qs(str(parsed.query))
        self.requests.append((path, query))
        if handler := self.handlers.get(path):
            return handler(request, query)
        if path == "/publications":
            return 200, {}, page_response(PUBLICATIONS)
        endpoint = path.split("/", 3)[-1]
        if self.serve_samples and endpoint in SAMPLES:
            return 200, {}, page_response([SAMPLES[endpoint]])
        return 200, {}, page_response([], total_pages=0)


@pytest.fixture(scope="session", autouse=True)
def mock_api() -> Iterator[MockApi]:
    """Mock the beehiiv API for the whole session, including class-scoped SDK tests."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield MockApi(mock)


@pytest.fixture
def api(mock_api: MockApi) -> Iterator[MockApi]:
    """A clean mocked API that returns empty lists unless a handler is registered."""
    mock_api.reset(serve_samples=False)
    yield mock_api
    mock_api.reset(serve_samples=True)
