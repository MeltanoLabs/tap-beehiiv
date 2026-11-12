# tap-beehiiv

`tap-beehiiv` is a Singer tap for the [beehiiv API](https://developers.beehiiv.com/) (v2), built with the [Meltano Singer SDK](https://sdk.meltano.com).

## Installation

```bash
uv tool install git+https://github.com/MeltanoLabs/tap-beehiiv.git
```

or add it to a Meltano project:

```yaml
plugins:
  extractors:
  - name: tap-beehiiv
    namespace: tap_beehiiv
    pip_url: git+https://github.com/MeltanoLabs/tap-beehiiv.git
```

## Settings

| Setting | Required | Default | Description |
|:--|:--|:--|:--|
| `api_key` | Yes | | beehiiv API key, sent as a Bearer token |
| `publication_ids` | No | All | Publication IDs to sync. Defaults to every publication the API key can access |
| `start_date` | No | | The earliest date to sync for date-filtered streams: `ad_network_reports` (by send date, defaults to all time) and `engagements` (defaults to 30 days ago) |
| `posts_expand` | No | `["stats"]` | Objects to expand on `posts`, e.g. `stats`, `free_web_content`, `premium_email_content` |
| `subscriptions_expand` | No | `["stats"]` | Objects to expand on `subscriptions`, e.g. `stats`, `custom_fields`, `referrals`, `newsletter_lists` |
| `emit_activate_version_messages` | No | `false` | Whether to emit `ACTIVATE_VERSION` messages for full-table streams, so that targets which support them remove records that no longer exist in beehiiv |

Settings can be supplied through environment variables prefixed with `TAP_BEEHIIV_` (see `.env.example`). Run `tap-beehiiv --about` for the full list, including the settings built in to the SDK (stream maps, flattening, batch).

## Streams

The tap lists the publications the API key can access on every run and syncs every other stream once per publication, so a new publication is picked up without a config change. Every record of a publication-scoped stream carries `publication_id`, taken from the request path.

| Stream | Endpoint | Primary key | Replication |
|:--|:--|:--|:--|
| `publications` | `/publications` | `id` | Full table |
| `posts` | `/publications/{id}/posts` | `publication_id`, `id` | Full table |
| `subscriptions` | `/publications/{id}/subscriptions` | `publication_id`, `id` | Full table |
| `ad_network_reports` | `/publications/{id}/ad_network/reports` | `publication_id`, `post_id`, `ad_link_id` | Full table |
| `engagements` | `/publications/{id}/engagements` | `publication_id`, `date` | Incremental on `date` |
| `authors` | `/publications/{id}/authors` | `publication_id`, `id` | Full table |
| `automations` | `/publications/{id}/automations` | `publication_id`, `id` | Full table |
| `custom_fields` | `/publications/{id}/custom_fields` | `publication_id`, `id` | Full table |
| `newsletter_lists` | `/publications/{id}/newsletter_lists` | `publication_id`, `id` | Full table |
| `polls` | `/publications/{id}/polls` | `publication_id`, `id` | Full table |
| `post_templates` | `/publications/{id}/post_templates` | `publication_id`, `id` | Full table |
| `referral_program` | `/publications/{id}/referral_program` | `publication_id`, `id` | Full table |
| `segments` | `/publications/{id}/segments` | `publication_id`, `id` | Full table |
| `tiers` | `/publications/{id}/tiers` | `publication_id`, `id` | Full table |

Notes:

- **Field names and types follow the API.** `id` is not renamed, and timestamps such as `created` and `publish_date` are Unix seconds.
- **Nested values are passed through as returned.** Objects and arrays such as `stats`, `authors` and `content_tags` are not flattened or filtered, so new keys beehiiv adds inside them reach the target.
- **Stats are all-time totals.** `stats` on publications, posts and subscriptions has no date breakdown. Use `engagements` for daily opens and clicks per publication.
- **`ad_network_reports.revenue` is a formatted currency string** (e.g. `"$12.34"`), as returned by the API.
- **Optional features are skipped, not fatal.** Every stream except `publications`, `posts` and `subscriptions` logs a warning and moves on to the next publication if the API answers `403 Forbidden`, for example when a publication is not part of the ad network.
- **`subscriptions.email` is personal data.** Use [stream maps](https://sdk.meltano.com/en/latest/stream_maps.html) to hash or drop it before it reaches the target if needed.

### Why most streams are full table

The beehiiv API has no "updated since" filter and no `updated` field on posts or subscriptions, while values such as status, tier and stats keep changing on existing records. Reading only new records would silently miss those changes, so these streams read everything on each run. Load them with an upsert on the primary key to keep one current row per record.

Records deleted in beehiiv simply stop appearing, so by default they remain in the target. To have them removed, set `emit_activate_version_messages` to `true`: full-table streams then emit `ACTIVATE_VERSION` messages, and targets that support them drop records not seen in the latest run.

A full-table stream keeps no bookmark, so a run that fails part-way starts that stream again from the beginning.

`engagements` is the exception: it is a daily time series, so the tap bookmarks the last `date` per publication and reads forward from it, re-reading the bookmarked day because the current day is still accumulating.

## API limits

- **Rate limit.** The allowance depends on the beehiiv plan and applies per API key. The tap reads the `RateLimit-Remaining` and `RateLimit-Reset` response headers, waits for the window to reset when the allowance is used up, and retries `429 Too Many Requests` after the reset. Use a key dedicated to the tap so other clients don't share its allowance.
- **Pagination.** Requests use `limit=100`, the maximum. `subscriptions` and `polls` use cursor pagination; page-based pagination on `subscriptions` is capped by the API at 100 pages. Other streams are page-based.
- **Expanded post content.** Expanding content on `posts` can make the API answer `202 Accepted` with `Retry-After` while it prepares the content. The tap waits and retries.

## Usage

```bash
tap-beehiiv --version
tap-beehiiv --help
tap-beehiiv --config CONFIG --discover > ./catalog.json
```

## Development

```bash
uv sync
uv run pytest
uv run tap-beehiiv --about
```

Tests run against a mocked API and need no credentials. Linting and type checks:

```bash
uvx ruff check .
uvx --with=tox-uv tox -e typing
```

### Testing with Meltano

```bash
python3 -m venv env && source env/bin/activate
pip install meltano
meltano install
cp .env.example .env  # then set TAP_BEEHIIV_API_KEY
meltano run tap-beehiiv target-jsonl
```
