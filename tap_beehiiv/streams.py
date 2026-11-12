"""Stream type classes for tap-beehiiv."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, ClassVar

from singer_sdk.pagination import BaseAPIPaginator, SinglePagePaginator

from tap_beehiiv.client import BeehiivCursorPaginator, BeehiivStream

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from singer_sdk.helpers.types import Context, Record

ENGAGEMENTS_MAX_DAYS = 31
ENGAGEMENTS_DEFAULT_LOOKBACK_DAYS = 30


class PublicationsStream(BeehiivStream):
    """Publications the API key can access. Parent of every other stream."""

    name = "publications"
    path = "/publications"
    primary_keys = ("id",)
    extra_params: ClassVar[dict[str, Any]] = {"expand[]": ["stats"]}

    @override
    def post_process(self, row: Record, context: Context | None = None) -> Record | None:
        publication_ids = self.config.get("publication_ids")
        if publication_ids and row["id"] not in publication_ids:
            # Dropping the record also skips its child streams
            return None
        return row

    @override
    def get_child_context(self, record: Record, context: Context | None) -> Context:
        return {"publication_id": record["id"]}


class PublicationChildStream(BeehiivStream):
    """Base class for streams scoped to a publication.

    `publication_id` is added to every record from the request path.
    """

    parent_stream_type = PublicationsStream
    primary_keys: Sequence[str] = ("publication_id", "id")
    # Full-table streams keep no per-publication state
    state_partitioning_keys: Sequence[str] | None = ()

    @override
    def post_process(self, row: Record, context: Context | None = None) -> Record | None:
        if context:
            row["publication_id"] = context["publication_id"]
        return row


class OptionalPublicationChildStream(PublicationChildStream):
    """Publication stream for a feature that not every publication or plan has."""

    tolerated_status_codes = (HTTPStatus.FORBIDDEN,)


class CursorPaginatedMixin:
    """Mixin for endpoints that paginate with `cursor`."""

    def get_new_paginator(self) -> BaseAPIPaginator[Any]:
        """Return a cursor paginator."""
        return BeehiivCursorPaginator()

    def get_url_params(
        self,
        context: Context | None,
        next_page_token: Any | None,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Send the page token as `cursor` instead of `page`."""
        params: dict[str, Any] = super().get_url_params(context, None)  # type: ignore[misc]
        if next_page_token is not None:
            params["cursor"] = next_page_token
        return params


class PostsStream(PublicationChildStream):
    """Posts, with all-time stats by default."""

    name = "posts"
    path = "/publications/{publication_id}/posts"

    @override
    def get_extra_params(self, context: Context | None) -> dict[str, Any]:
        return {"expand[]": self.config.get("posts_expand", ["stats"])}


class SubscriptionsStream(CursorPaginatedMixin, PublicationChildStream):
    """Subscriptions, with all-time stats by default.

    The API has no way to request only subscriptions changed since a point in time,
    so every run reads all of them.
    """

    name = "subscriptions"
    path = "/publications/{publication_id}/subscriptions"

    @override
    def get_extra_params(self, context: Context | None) -> dict[str, Any]:
        return {"expand[]": self.config.get("subscriptions_expand", ["stats"])}


class AdNetworkReportsStream(OptionalPublicationChildStream):
    """Ad network report rows: one per ad link per post."""

    name = "ad_network_reports"
    path = "/publications/{publication_id}/ad_network/reports"
    primary_keys = ("publication_id", "post_id", "ad_link_id")

    @override
    def get_extra_params(self, context: Context | None) -> dict[str, Any]:
        start_date = _config_start_date(self.config)
        return {"start_date": start_date.isoformat()} if start_date else {}


class EngagementsStream(OptionalPublicationChildStream):
    """Daily email engagement metrics per publication.

    Incremental on `date`. The API returns at most 31 days per request, so the range
    from the bookmark (or `start_date`) to today is read in windows. The bookmarked day
    is read again on the next run because the current day is still accumulating.
    """

    name = "engagements"
    path = "/publications/{publication_id}/engagements"
    primary_keys = ("publication_id", "date")
    replication_key = "date"
    is_sorted = True
    # Bookmarks are kept per publication
    state_partitioning_keys = None

    _window_start: date
    _window_days: int

    @override
    def get_new_paginator(self) -> BaseAPIPaginator[Any]:
        return SinglePagePaginator()

    @override
    def get_url_params(
        self,
        context: Context | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        return {
            "start_date": self._window_start.isoformat(),
            "number_of_days": self._window_days,
            "granularity": "day",
            "direction": "asc",
        }

    def _start_date(self, context: Context | None) -> date:
        bookmark = self.get_starting_replication_key_value(context)
        if bookmark:
            return date.fromisoformat(str(bookmark)[:10])
        return _config_start_date(self.config) or (
            _today() - timedelta(days=ENGAGEMENTS_DEFAULT_LOOKBACK_DAYS)
        )

    @override
    def get_records(self, context: Context | None) -> Iterable[dict[str, Any]]:
        start = self._start_date(context)
        today = _today()
        while start <= today:
            self._window_start = start
            self._window_days = min(ENGAGEMENTS_MAX_DAYS, (today - start).days + 1)
            yield from super().get_records(context)
            start += timedelta(days=self._window_days)


class AuthorsStream(OptionalPublicationChildStream):
    """Post authors."""

    name = "authors"
    path = "/publications/{publication_id}/authors"


class AutomationsStream(OptionalPublicationChildStream):
    """Automations."""

    name = "automations"
    path = "/publications/{publication_id}/automations"


class CustomFieldsStream(OptionalPublicationChildStream):
    """Custom subscriber fields defined on a publication."""

    name = "custom_fields"
    path = "/publications/{publication_id}/custom_fields"


class NewsletterListsStream(OptionalPublicationChildStream):
    """Newsletter lists."""

    name = "newsletter_lists"
    path = "/publications/{publication_id}/newsletter_lists"


class PollsStream(CursorPaginatedMixin, OptionalPublicationChildStream):
    """Polls, with aggregate vote stats."""

    name = "polls"
    path = "/publications/{publication_id}/polls"
    extra_params: ClassVar[dict[str, Any]] = {"expand[]": ["stats"]}


class PostTemplatesStream(OptionalPublicationChildStream):
    """Post templates."""

    name = "post_templates"
    path = "/publications/{publication_id}/post_templates"


class ReferralProgramStream(OptionalPublicationChildStream):
    """Referral program milestones."""

    name = "referral_program"
    path = "/publications/{publication_id}/referral_program"


class SegmentsStream(OptionalPublicationChildStream):
    """Segments, with their most recently calculated stats."""

    name = "segments"
    path = "/publications/{publication_id}/segments"
    extra_params: ClassVar[dict[str, Any]] = {"expand[]": ["stats"]}


class TiersStream(OptionalPublicationChildStream):
    """Premium tiers, with stats and prices."""

    name = "tiers"
    path = "/publications/{publication_id}/tiers"
    extra_params: ClassVar[dict[str, Any]] = {"expand[]": ["stats", "prices"]}


def _today() -> date:
    return datetime.now(tz=timezone.utc).date()


def _config_start_date(config: Mapping[str, Any]) -> date | None:
    """Return the date part of the `start_date` setting, if set."""
    value = config.get("start_date")
    return date.fromisoformat(str(value)[:10]) if value else None


STREAM_TYPES: tuple[type[BeehiivStream], ...] = (
    PublicationsStream,
    PostsStream,
    SubscriptionsStream,
    AdNetworkReportsStream,
    EngagementsStream,
    AuthorsStream,
    AutomationsStream,
    CustomFieldsStream,
    NewsletterListsStream,
    PollsStream,
    PostTemplatesStream,
    ReferralProgramStream,
    SegmentsStream,
    TiersStream,
)
