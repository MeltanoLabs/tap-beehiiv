"""REST client handling, including the BeehiivStream base class."""

from __future__ import annotations

import sys
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, ClassVar

from singer_sdk import SchemaDirectory, StreamSchema
from singer_sdk.authenticators import BearerTokenAuthenticator
from singer_sdk.exceptions import RetriableAPIError
from singer_sdk.pagination import BaseAPIPaginator, PageNumberPaginator
from singer_sdk.streams import RESTStream

from tap_beehiiv import schemas

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    import requests
    from singer_sdk.helpers.types import Context

SCHEMAS_DIR = SchemaDirectory(schemas)

PAGE_SIZE = 100
MAX_THROTTLE_SECONDS = 60.0
DEFAULT_RETRY_SECONDS = 5.0


class BeehiivPagePaginator(PageNumberPaginator):
    """Paginator for endpoints using `page` / `total_pages`."""

    @override
    def get_next(self, response: requests.Response) -> int | None:
        total_pages = response.json().get("total_pages") or 0
        return self.current_value + 1 if self.current_value < total_pages else None


class BeehiivCursorPaginator(BaseAPIPaginator[str | None]):
    """Paginator for endpoints using `cursor` / `has_more` / `next_cursor`."""

    def __init__(self) -> None:
        """Start without a cursor, which requests the first page."""
        super().__init__(None)

    @override
    def get_next(self, response: requests.Response) -> str | None:
        body = response.json()
        # The fields are top-level on list endpoints; tolerate the nested shape too.
        page_info = body.get("pagination") or body
        return page_info.get("next_cursor") if page_info.get("has_more") else None


class BeehiivStream(RESTStream[Any]):
    """Base class for beehiiv streams."""

    url_base = "https://api.beehiiv.com/v2"
    records_jsonpath = "$.data[*]"
    schema = StreamSchema(SCHEMAS_DIR)

    #: Query parameters sent with every request for this stream.
    extra_params: ClassVar[dict[str, Any]] = {}

    #: HTTP error statuses that end the stream (for the current publication) with a
    #: warning instead of failing the sync, e.g. a feature the publication doesn't have.
    tolerated_status_codes: ClassVar[tuple[int, ...]] = ()

    @property
    @override
    def authenticator(self) -> BearerTokenAuthenticator:
        return BearerTokenAuthenticator(token=self.config["api_key"])

    @override
    def get_new_paginator(self) -> BaseAPIPaginator[Any]:
        return BeehiivPagePaginator(start_value=1)

    def get_extra_params(self, context: Context | None) -> dict[str, Any]:  # noqa: ARG002
        """Return stream-specific query parameters."""
        return dict(self.extra_params)

    @override
    def get_url_params(
        self,
        context: Context | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": PAGE_SIZE, **self.get_extra_params(context)}
        if next_page_token is not None:
            params["page"] = next_page_token
        return params

    def _throttle(self, response: requests.Response) -> None:
        """Wait for the rate limit window to reset once the allowance is used up.

        The allowance differs by plan, so it is read from the `RateLimit-*` response
        headers rather than assumed.
        """
        remaining = response.headers.get("RateLimit-Remaining")
        if remaining is None or not remaining.isdigit() or int(remaining) > 0:
            return
        wait = _seconds_until_reset(response)
        self.logger.info("Rate limit allowance used up, waiting %.1fs for reset", wait)
        time.sleep(wait)

    @override
    def validate_response(self, response: requests.Response) -> None:
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            msg = self.response_error_message(response)
            raise RetriableAPIError(msg, response)

        self._throttle(response)

        # Returned while requested (expanded) content is still being prepared
        if response.status_code == HTTPStatus.ACCEPTED:
            msg = f"202 Accepted (content not ready) for path: {response.request.path_url}"
            raise RetriableAPIError(msg, response)

        if response.status_code in self.tolerated_status_codes:
            return

        super().validate_response(response)

    @override
    def parse_response(self, response: requests.Response) -> Iterable[dict[str, Any]]:
        if response.status_code in self.tolerated_status_codes:
            self.logger.warning(
                "Skipping '%s' for %s: HTTP %d %s",
                self.name,
                response.request.path_url.split("?")[0],
                response.status_code,
                response.reason,
            )
            return
        yield from super().parse_response(response)

    @override
    def backoff_wait_generator(self) -> Generator[float, None, None]:
        return self.backoff_runtime(value=_retry_wait)

    @override
    def backoff_jitter(self, value: float) -> float:
        # Waits come from the API's own headers, so no jitter is added
        return value

    @override
    def backoff_max_tries(self) -> int:
        return 8


def _seconds_until_reset(response: requests.Response) -> float:
    """Return the seconds until the rate limit window resets, per `RateLimit-Reset`."""
    reset = response.headers.get("RateLimit-Reset", "")
    if not reset.isdigit():
        return DEFAULT_RETRY_SECONDS
    return min(max(int(reset) - time.time(), 0) + 1, MAX_THROTTLE_SECONDS)


def _retry_wait(exception: Exception) -> float:
    """Return the seconds to wait before retrying a failed request."""
    response: requests.Response | None = getattr(exception, "response", None)
    if response is None:
        return DEFAULT_RETRY_SECONDS
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(float(retry_after), MAX_THROTTLE_SECONDS)
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return _seconds_until_reset(response)
    return DEFAULT_RETRY_SECONDS
