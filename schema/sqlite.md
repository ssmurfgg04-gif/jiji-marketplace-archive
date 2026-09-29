# SQLite catalogue schema

`jiji.db` is the release catalogue. Its primary analytical tables are:

| Table | Primary identifier | Purpose |
| --- | --- | --- |
| `listings` | `guid` | Current/live listing observations with first/last-seen timestamps. |
| `price_history` | `(guid, seen_at)` | Attributable price observations from the live collector. |
| `runs` | `id` | Collection-run ledger. |
| `slug_state` | `slug` | Category sweep state. |
| `warc_item` | `url` | Enriched Common Crawl item captures. |

`listings_fts` is an SQLite FTS index over title and description; its implementation tables are not intended for direct analysis.

Every record's `source` identifies its collection layer: `jiji-api-live` or `commoncrawl-warc`. The `suspicious` field is nullable and, when set, contains a textual class such as `bait-price`, `overpriced`, or `spec-fraud`.
