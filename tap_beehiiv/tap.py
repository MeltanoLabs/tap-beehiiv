"""beehiiv tap class."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from singer_sdk import Tap
from singer_sdk import typing as th  # JSON schema typing helpers

from tap_beehiiv import streams

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from singer_sdk import Stream


class TapBeehiiv(Tap):
    """Singer tap for beehiiv."""

    name = "tap-beehiiv"

    config_jsonschema = th.PropertiesList(
        th.Property(
            "api_key",
            th.StringType(nullable=False),
            required=True,
            secret=True,
            title="API Key",
            description="beehiiv API key, sent as a Bearer token",
        ),
        th.Property(
            "publication_ids",
            th.ArrayType(th.StringType),
            title="Publication IDs",
            description=(
                "Publication IDs to sync. Defaults to every publication the API key can access."
            ),
        ),
        th.Property(
            "start_date",
            th.DateTimeType,
            title="Start Date",
            description=(
                "The earliest date to sync for date-filtered streams: `ad_network_reports` "
                "(by send date, defaults to all time) and `engagements` (defaults to 30 days ago)"
            ),
        ),
        th.Property(
            "posts_expand",
            th.ArrayType(th.StringType),
            default=["stats"],
            title="Posts Expand",
            description=(
                "Objects to expand on `posts`, e.g. `stats`, `free_web_content`, "
                "`premium_email_content`. Expanding content makes the sync considerably slower."
            ),
        ),
        th.Property(
            "subscriptions_expand",
            th.ArrayType(th.StringType),
            default=["stats"],
            title="Subscriptions Expand",
            description=(
                "Objects to expand on `subscriptions`, e.g. `stats`, `custom_fields`, "
                "`referrals`, `newsletter_lists`"
            ),
        ),
        th.Property(
            "emit_activate_version_messages",
            th.BooleanType,
            default=False,
            title="Emit Activate Version Messages",
            description=(
                "Whether to emit `ACTIVATE_VERSION` messages for full-table streams, so that "
                "targets which support them remove records that no longer exist in beehiiv"
            ),
        ),
    ).to_dict()

    @override
    def discover_streams(self) -> list[Stream]:
        """Return a list of discovered streams."""
        return [stream_type(tap=self) for stream_type in streams.STREAM_TYPES]


if __name__ == "__main__":
    TapBeehiiv.cli()
