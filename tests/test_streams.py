"""Tests for pagination, publication scoping, rate limiting and error handling."""

from __future__ import annotations

import io
import json
import time
from contextlib import redirect_stdout
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tap_beehiiv import streams
from tap_beehiiv.tap import TapBeehiiv
from tests.conftest import page_response

if TYPE_CHECKING:
    from tests.conftest import MockApi

CONFIG: dict[str, Any] = {"api_key": "test_api_key"}


def sync(
    selected: set[str],
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Sync the selected streams and return the records per stream and the final state."""
    tap = TapBeehiiv(config={**CONFIG, **(config or {})}, state=state)
    for name, stream in tap.streams.items():
        stream.selected = name in selected
    out = io.StringIO()
    with redirect_stdout(out):
        tap.sync_all()
    records: dict[str, list[dict[str, Any]]] = {}
    final_state: dict[str, Any] = {}
    for line in out.getvalue().splitlines():
        message = json.loads(line)
        if message["type"] == "RECORD":
            records.setdefault(message["stream"], []).append(message["record"])
        elif message["type"] == "STATE":
            final_state = message["value"]
    return records, final_state


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleeps instead of waiting."""
    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", calls.append)
    return calls


def test_page_pagination_and_publication_id(api: MockApi) -> None:
    """Posts are read page by page and get `publication_id` from the request path."""

    def posts(_request: object, query: dict[str, list[str]]) -> tuple[int, dict[str, str], str]:
        page = int(query.get("page", ["1"])[0])
        return 200, {}, page_response([{"id": f"post_{page}"}], page=page, total_pages=3)

    api.on("/publications/pub_1/posts", posts)

    records, _ = sync({"posts"})

    assert records["posts"] == [
        {"publication_id": "pub_1", "id": "post_1"},
        {"publication_id": "pub_1", "id": "post_2"},
        {"publication_id": "pub_1", "id": "post_3"},
    ]
    calls = api.calls("/publications/pub_1/posts")
    assert [call.get("page") for call in calls] == [["1"], ["2"], ["3"]]
    assert all(call["limit"] == ["100"] and call["expand[]"] == ["stats"] for call in calls)
    # The second publication is requested too
    assert len(api.calls("/publications/pub_2/posts")) == 1


def test_cursor_pagination(api: MockApi) -> None:
    """Subscriptions follow `next_cursor` until `has_more` is false."""

    def subscriptions(
        _request: object, query: dict[str, list[str]]
    ) -> tuple[int, dict[str, str], str]:
        if "cursor" not in query:
            body = {"data": [{"id": "sub_1"}], "has_more": True, "next_cursor": "abc"}
        else:
            body = {"data": [{"id": "sub_2"}], "has_more": False, "next_cursor": None}
        return 200, {}, json.dumps(body)

    api.on("/publications/pub_2/subscriptions", subscriptions)

    records, _ = sync(
        {"subscriptions"},
        config={"subscriptions_expand": ["stats", "custom_fields"]},
    )

    assert records["subscriptions"] == [
        {"publication_id": "pub_2", "id": "sub_1"},
        {"publication_id": "pub_2", "id": "sub_2"},
    ]
    calls = api.calls("/publications/pub_2/subscriptions")
    assert [call.get("cursor") for call in calls] == [None, ["abc"]]
    assert all("page" not in call for call in calls)
    assert calls[0]["expand[]"] == ["stats", "custom_fields"]


def test_publication_ids_filter(api: MockApi) -> None:
    """Only the configured publications are emitted and used for child streams."""
    records, _ = sync({"publications", "posts"}, config={"publication_ids": ["pub_2"]})

    assert [record["id"] for record in records["publications"]] == ["pub_2"]
    assert api.calls("/publications/pub_1/posts") == []
    assert len(api.calls("/publications/pub_2/posts")) == 1


def test_forbidden_optional_stream_is_skipped(api: MockApi) -> None:
    """A 403 on an optional stream skips that publication without failing the sync."""
    api.on(
        "/publications/pub_1/ad_network/reports",
        lambda _request, _query: (403, {}, json.dumps({"status": 403})),
    )
    report = {"publication_id": "pub_2", "post_id": "post_1", "ad_link_id": "a", "revenue": "$1.00"}
    api.on(
        "/publications/pub_2/ad_network/reports",
        lambda _request, _query: (200, {}, page_response([report])),
    )

    records, _ = sync({"ad_network_reports"}, config={"start_date": "2026-01-15T00:00:00Z"})

    assert records["ad_network_reports"] == [report]
    assert api.calls("/publications/pub_2/ad_network/reports")[0]["start_date"] == ["2026-01-15"]


def test_forbidden_core_stream_fails(api: MockApi) -> None:
    """A 403 on a core stream is an error."""
    api.on(
        "/publications/pub_1/posts",
        lambda _request, _query: (403, {}, json.dumps({"status": 403})),
    )

    with pytest.raises(Exception, match="403"):
        sync({"posts"})


def test_rate_limited_request_is_retried(api: MockApi, sleeps: list[float]) -> None:
    """A 429 is retried after waiting for the rate limit window to reset."""
    attempts: list[int] = []

    def posts(_request: object, _query: object) -> tuple[int, dict[str, str], str]:
        attempts.append(1)
        if len(attempts) == 1:
            return 429, {"RateLimit-Reset": str(int(time.time()) + 20)}, "{}"
        return 200, {}, page_response([{"id": "post_1"}])

    api.on("/publications/pub_1/posts", posts)

    records, _ = sync({"posts"})

    assert records["posts"] == [{"publication_id": "pub_1", "id": "post_1"}]
    assert len(attempts) == 2  # noqa: PLR2004
    assert any(15 < wait <= 22 for wait in sleeps)  # noqa: PLR2004


def test_waits_when_allowance_is_used_up(api: MockApi, sleeps: list[float]) -> None:
    """The tap waits for the window to reset when no requests remain."""
    headers = {"RateLimit-Remaining": "0", "RateLimit-Reset": str(int(time.time()) + 30)}
    api.on(
        "/publications/pub_1/posts",
        lambda _request, _query: (200, headers, page_response([{"id": "post_1"}])),
    )

    sync({"posts"})

    assert any(25 < wait <= 32 for wait in sleeps)  # noqa: PLR2004


def test_content_not_ready_is_retried(api: MockApi, sleeps: list[float]) -> None:
    """A 202 for expanded content is retried after `Retry-After`."""
    attempts: list[int] = []

    def posts(_request: object, _query: object) -> tuple[int, dict[str, str], str]:
        attempts.append(1)
        if len(attempts) == 1:
            return 202, {"Retry-After": "7"}, json.dumps({"data": {"state": "pending"}})
        return 200, {}, page_response([{"id": "post_1"}])

    api.on("/publications/pub_1/posts", posts)

    records, _ = sync({"posts"}, config={"posts_expand": ["stats", "free_web_content"]})

    assert len(records["posts"]) == 1
    assert 7.0 in sleeps  # noqa: PLR2004


def test_engagements_windows_and_bookmark(api: MockApi, monkeypatch: pytest.MonkeyPatch) -> None:
    """Engagements are read in 31-day windows and resume from the bookmark."""
    today = date(2026, 3, 10)
    monkeypatch.setattr(streams, "_today", lambda: today)

    def engagements(
        _request: object, query: dict[str, list[str]]
    ) -> tuple[int, dict[str, str], str]:
        start = date.fromisoformat(query["start_date"][0])
        days = int(query["number_of_days"][0])
        data = [
            {"date": (start + timedelta(days=n)).isoformat(), "total_opens": n}
            for n in (0, days - 1)
        ]
        return 200, {}, json.dumps({"data": data})

    api.on("/publications/pub_1/engagements", engagements)
    api.on("/publications/pub_2/engagements", engagements)

    records, state = sync({"engagements"}, config={"start_date": "2026-01-01T00:00:00Z"})

    calls = api.calls("/publications/pub_1/engagements")
    assert [(call["start_date"][0], call["number_of_days"][0]) for call in calls] == [
        ("2026-01-01", "31"),
        ("2026-02-01", "31"),
        ("2026-03-04", "7"),
    ]
    assert records["engagements"][0] == {
        "publication_id": "pub_1",
        "date": "2026-01-01",
        "total_opens": 0,
    }
    partitions = state["bookmarks"]["engagements"]["partitions"]
    assert {p["context"]["publication_id"] for p in partitions} == {"pub_1", "pub_2"}
    assert all(p["replication_key_value"] == "2026-03-10" for p in partitions)

    # The next run starts from the bookmarked day
    api.requests.clear()
    monkeypatch.setattr(streams, "_today", lambda: today + timedelta(days=1))
    sync({"engagements"}, state=state)

    calls = api.calls("/publications/pub_1/engagements")
    assert [(call["start_date"][0], call["number_of_days"][0]) for call in calls] == [
        ("2026-03-10", "2"),
    ]


def test_activate_version_is_opt_in(api: MockApi) -> None:  # noqa: ARG001
    """Full-table streams emit ACTIVATE_VERSION only when asked to; incremental ones never."""

    def message_streams(config: dict[str, Any]) -> set[str]:
        tap = TapBeehiiv(config={**CONFIG, **config})
        out = io.StringIO()
        with redirect_stdout(out):
            tap.sync_all()
        messages = [json.loads(line) for line in out.getvalue().splitlines()]
        return {m["stream"] for m in messages if m["type"] == "ACTIVATE_VERSION"}

    assert message_streams({}) == set()

    activated = message_streams({"emit_activate_version_messages": True})
    assert {"publications", "posts", "subscriptions", "ad_network_reports"} <= activated
    assert "engagements" not in activated
