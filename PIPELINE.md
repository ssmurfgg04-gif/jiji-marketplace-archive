# Jiji Snapshot Pipeline

How the full Jiji dataset gets built, updated daily, and queried. Three layers,
cheapest-first: nothing below ever touches Jiji's servers unless the layers
above can't answer.

## Layer 0 — Bulk history (Common Crawl + Wayback, zero Jiji packets)

| Piece | Script | Input → Output |
|---|---|---|
| CDX index collect | `cc_index_collect.py` | index.commoncrawl.org → `cc_index/<domain>.jsonl` (resumeable, skips cached crawls) |
| Fetch manifest | `scripts/cc_layer0.py` | `cc_index/*.jsonl` → `cc_manifest/manifest.parquet` (url, capture_ts, crawl, warc file+offset+length). Filter `--domain`, `--min-year` |
| WARC fetch workers | `cc_fetch*.py` | manifest ranges → `cc_bodies/*.gz` + `queue.jsonl` commit log |
| Parse to rows | `cc_parse.py` | bodies (API JSON or HTML cards) → `cc_rows.tsv` (resumeable via bodyhash + queue log) |
| Partition/enrich | `cc_partition.py`, `cc_meta_build.py`, `cc_merge_worker_rows.py` | → `jiji_mined_dataset_*.parquet` (price history, sellers, views) |
| Wayback backfill | `wayback_miner.py`, `jiji_wayback_csv.py` | gaps CC missed |

Current holdings: `cc_index/jiji.co.ke.jsonl` 209,937 captures (2019–2025, 55 crawls),
`cc_rows.tsv` ~172MB, mined snapshots up to **485,830 rows** (2026-08-15).

Network notes (verified 2026-09-27): `data.commoncrawl.org` reachable, but the
CDX index API and S3 bucket listings are blocked from some networks — run
`cc_index_collect.py` from GitHub Actions or any allowed network instead.
DuckDB-over-columnar-index needs the same access; `cc_layer0.py` works fully
offline on already-collected jsonl.

## Layer 1 — Live incremental (`api_web` JSON, the only layer touching Jiji)

`scripts/jiji_incremental.py` — polite single worker (random 3–8s delays,
retries, one query at a time):

```bash
python scripts/jiji_incremental.py --query "ddr3 desktop ram" --max-pages 3 --sort new     # daily delta
python scripts/jiji_incremental.py --query "ddr3 desktop ram" --sort price --max-pages 2   # deal scan
python scripts/jiji_incremental.py --slugs-file data/category_slugs.txt --max-pages 2     # deep: whole category pools
```

Category tree: `data/category_tree.tsv` (230 nodes, slug/id/parent/name/depth),
harvested from `api_web/v1/categories` by `scripts/jiji_tree.py` — use it as
the `--slugs-file` seed for full-site coverage. Note: numeric slugs like
`284-processors` are NOT categories but category+type views (`284` =
computer-hardware id, `16` = computers-and-laptops id); mining the parent
category slug covers the same pool.

Dates on everything: live rows carry `first_seen`/`last_seen` (UTC ISO);
mined rows carry `capture_ts`/`first_seen`/`last_seen`; manifest rows carry
`capture_ts` + crawl id. Every price is attributable to a date — no dateless rows.

- Hits `api_web/v1/listing` (`?query=&slug=&sort=new|price|rel&page=`), ~10x
  lighter than HTML, schema-stable. Sort discovery: page HTML embeds the
  endpoint pattern (`sort=price` = cheapest-first = deal-hunter view).
- `data/jiji.db` (SQLite): `listings` + `price_history` + `runs`.
- BLAKE3 over canonical record → only changed rows touch history.
- Daily snapshot: `data/snapshot-YYYY-MM-DD.parquet` via DuckDB.
- All outputs live under `data/` (gitignored). Never commit snapshots.

## Querying history + live together (DuckDB bridge)

```sql
SELECT 'mined' src, price_kes price, title
FROM 'jiji_mined_dataset_20260815_074727.parquet' WHERE lower(title) LIKE '%ddr3%'
UNION ALL
SELECT 'live', price, title FROM 'data/snapshot-2026-09-27.parquet'
WHERE lower(title) LIKE '%ddr3%';
```

## Deal-hunter rule (feeds `jiji-deal-hunter`)

Flag when `live.price < 0.6 × trailing_category_average AND seller verified`.
Always verify in shop (CPU-Z capacity, SMART health) — page-1 averages are
polluted by bait (e.g. 16GB DDR4 @ 4,499) and RGB-kit premiums.

## Unblocking CDX / S3 discovery (verified 2026-09-27)

`index.commoncrawl.org` and S3 bucket listings time out from some residential
networks (yesterday the same calls worked — classic rate-limit cooldown).
Fixes, in order:

1. **Run discovery from Actions** (`.github/workflows/cc-index.yml`, weekly
   cron + manual dispatch). Different egress, resumeable collector, results as
   Artifacts; curated Parquet promotes to `jiji-wayback-dataset` via secret.
2. **S3 via the real endpoint**, not the CloudFront frontend:
   `aws s3 ls s3://commoncrawl/cc-index/ --no-sign-request --recursive`
   (needs AWS CLI; directory listing works on S3 proper even when
   `data.commoncrawl.org/?list-type=2` serves HTML).
3. **Retry posture**: the collector already resumes per-crawl; add night-time
   reruns with jitter before assuming a block. Never parallelize discovery —
   one polite worker, or the block becomes permanent.

## Coverage ledger (measured 2026-09-27)

| Slice | Count | Notes |
|---|---|---|
| CDX captures, jiji.co.ke | 209,937 rows / **138,345 distinct URLs**, 2019–2025, 55 crawls | `cc_index/jiji.co.ke.jsonl` (109MB) |
| CDX captures, jiji.ng | 266MB jsonl | sister market, same pipeline |
| Parsed rows `cc_rows.tsv` | **1,571,727 lines** | all domains, HTML+API bodies |
| Mined snapshot 2026-08-15 | **485,830 rows** (481,814 KE + 2,224 GH + 1,735 NG + 57 TZ), all distinct guids, last_seen ≤ 2025-04-18 | rich schema: price history, sellers, views |
| Live snapshot 2026-09-27 | 40 rows (seed run) | grows with daily runs |

Honest denominator: Jiji turns listings over in weeks, so cumulative 2019–2026
unique KE listings likely number in the low millions; ~482K mined KE rows is a
large minority of *archived* history, strongest on popular categories (they get
recrawled) and thin on long-tail. Live layer covers the present from today on.
Gap: mined frozen 2025-04-18, live starts 2026-09-27 — May'25–Aug'26 covered
only by Saturday Wayback backfill (`listings.tsv`).
Gap inquest 2026-09-29: CC crawls May-Oct 2025 contain ZERO Jiji item captures
(checked manifest: 6 crawls, 0 .html items — premise refuted, nothing to
enrich); post-Oct-2025 CC index API throttled, retry tomorrow; Wayback gap
CDX holds 6 captures total (+0 rows, dry hole); 0 gap survivors among 611
dated live rows (turnover kills); no third-party dumps exist (scraper code
only). Policy: NEVER interpolate — one synthetic row breaks the dataset's
pitch. Floor: 1,000 rows/month/layer; see coverage.py matrix.

## Price-sanity policy (2026-09-28)

Ingest rejects TRUE garbage only: price <= 0 or price > 1B (phone-number
artifacts). NULL (no-price/contact) passes through; genuine micro-prices
(<200) and genuine high-value assets (villas/petrol stations to ~350M) are
kept. The 200–20M band applies at ANALYSIS time, not ingest. Enforced in
`scripts/jiji_incremental.py::norm` (skipped, counted as price_rejected) and
`wayback_miner.py::sane_price` (keep/value/garbage tri-state). Live DB purged
of 2 sub-zero/billion rows on 2026-09-28.

## S-grade program (2026-09-29)

Definition: correct identity + normalized product + price + condition +
description + location + timestamp + history + provenance, duplicates/anomalies
detected, missingness measured, suspicious flagged, reproducible.

- Identity: URL/guid PKs, 0 duplicates (verified by audit).
- Normalized product: brand + family + model canonicalization
  (`normalized_product`, temp `normalize.py` — promote to scripts/ if rerun
  needed). Ceilings are honest: laptops 79% (rest accessories), cars 56%
  (vague dealer titles), fashion/jobs correctly empty. Category-aware rubric.
- Price: ingest band (drop <=0/>1B); `suspicious` flags via per-product bands
  (bait <30% median, over >300%, min 5 samples) + spec-fraud map
  (premium-model + budget-CPU pairs, e.g. T480+Celeron).
- Condition: structured attr + `condition_inferred` text rules (ex-uk, second
  hand, brand new...). Union 50% warc / 29% live — structural (sellers skip it).
- Live seller 22.9% is an API ceiling (private sellers hidden); item.py
  recovers named sellers per machine (tech cats 30% -> 95% via --missing
  pass, 985 rows, Sep 29).
- Provenance: `source` column on listings/warc_item/price_history
  (jiji-api-live / commoncrawl-warc).
- S-grade audit Sep 29 eve: warc 108,680 rows 0 dups, price 92% sane,
  cat/seller/region 99%+, condition union 50% (laptops 79%, jobs structural
  zero), normalized category-aware (laptops 79%, accessories correctly empty),
  1,436 suspicious flagged, 12.5K price rows / 697 runs. Grade: A overall,
  S on identity/location/history/provenance/reproducibility.
- Hard lessons: (1) ALTER-appended columns swap positional INSERTs — always
  name columns explicitly (68,916 rows repaired 2026-09-29). (2) FTS/trigger
  migrations must handle existing tables. (3) PRAGMA table_info needs
  `PRAGMA schema.table_info(tbl)` form — `table_info(db.tbl)` is a syntax
  error (broke first merge). (4) Detached runs need log files or failures are
  invisible; foreground pipes truncated by Select-Object kill processes.
  (5) SQLite has no median() — group in Python.

## Comments section (2026-09-29)

Seller descriptions now captured: `listings.description` (short text, free in
list payload, backfills on every sweep via hash change), `description_full` +
`date_created` + `views` via `scripts/item.py` (on-demand
`api_web/v1/item/{guid}`, 1 worker, 2s delay). FTS index covers
title+description. `scripts/flagged.py` scans ~45 fault patterns (dead, broken
screen, no battery, for parts, as-is, missing parts, needs repair) — caught
the Dynabook dot/no-battery, Acer dead LCD, Asus dead board on day one.
`python scripts/item.py --flagged --max 50` enriches flagged rows to full text.

## WARC enrichment eras (2026-09-29)

Two Jiji templates: post-2023 (itemprop/seller-block/breadcrumb markup) and
pre-2023 (OG `product:price:amount`, `product:condition`, `product:brand`,
meta-description seller text, title-tag seller/region). `extract()` tries
modern first, OG fallback second, title-tag last. `warc_item.description`
holds the meta seller text. `--refetch-empty` reprocesses rows missing
condition+category after extractor upgrades.

`scripts/warc_fetch.py` fetches CC byte-ranges for the latest capture of each
of the 111,422 distinct item (.html) URLs and extracts title, price
(`itemprop=price`), condition (`itemprop=itemCondition`), seller + sellerpage +
Verified-ID badge, breadcrumb category, title-tag region, all `b-advert-attribute`
pairs → `data/jiji.db:warc_item` (url PK, resume-safe). Task `JijiWarcFetch`
daily 04:30, `--max 2000 --delay 0.8` (~8 weeks to drain, then idles). Log:
`data/logs/warc.log`.

## Engine v2 + runner drain (2026-09-29)

- `warc_fetch.py` is now queue+workers: `--workers 8`, adaptive Throttle
  (0.3s start, x2 backoff on 429/503, recovery on streaks), thread-local
  persistent sessions, single writer with `--batch 250` commits, hot-category
  priority ordering, `--shard-file/--out-db` runner mode.
- Measured local: **150 rows, 8 workers, 0 throttles, 9.3 rows/s in 16s**
  (~14x v1's ~0.65 rows/s). Full 107K projects ~3.2h one machine.
- Digest-dedup measured: 122,992 rows / 122,992 distinct digests — zero
  duplicates, no free lunch (byte-range fetching was already the design).
- `.github/workflows/warc-drain.yml`: 20-shard matrix (`data/warc_shards/`),
  4 workers each, sqlite parts as artifacts; merge via `scripts/warc_merge.py`
  after `gh run download`. Trigger: Actions tab, Run workflow.
- OVERRIDE (owner-approved 2026-09-29): runner scraping permitted for this
  one-time drain despite the Azure-IP rule above; keep 4 workers + 0.5s start
  delay, do not raise without re-approval.
- Live collector delta mode: `slug_state` totals; `sort=new` breaks after
  page 1 when total unchanged and zero new rows (page-1-always, deep-on-change).

## Image pHash layer (2026-10-11)

Byte hashes (WARC bodyhash, BLAKE3 records) never fire when the SAME photo is
re-uploaded across listings (T490 title + T510 photos). `scripts/phash_images.py`
closes it: 64-bit dHash (pure PIL, zero new deps), single polite worker,
resume-safe `data/image_hashes.db` (kept OUT of `jiji.db` so the home-runner
release zip can never lock it).

```bash
python scripts/phash_images.py --guid <guid> [--guid ...]   # spot-checks
python scripts/phash_images.py --slug computers-and-laptops --sort price --max-pages 2
```

Reports exact-reuse groups (one photo, 2+ listings = stock/reused bait) plus
near-dupes (hamming ≤5, tune tighter for dark product shots). Verified 2026-10-11:
154 images / 40 cheapest listings hashed, pipeline correct, 0 reuse clusters in
that slice — fraud catch needs fleet-scale sharding (shard by slugs file, same
pattern as warc-drain). OCR badge-vs-title check deferred by owner decision.

## External findings on this data (2026-10-11, cited briefly)

An independent review of the mined snapshot reports (reproduced here with
caveats, not re-verified end to end):

- **Hedonic car index (strongest result):** make/model/year parsed for 5,760
  cars (85 make-models, R² 0.92); like-for-like asking prices +13% (2020),
  +28% (2021), +48% (2022), +65% (2023) vs 2019 — above the shilling's slide,
  so FX pass-through + freight + duty/valuation changes are candidate drivers
  (hypothesis, not finding). 2019 base noisy (n=211); 2024 unestimable (blank
  titles). Depreciation within model ≈ -48% at age 10, ≈ -70% at age 15 vs a
  5-year-old car.
- **Price-cut warning (accepted):** among multi-capture rows ~80% show a
  "change" with median +0.1% and ±hundreds-% tails — a cross-source parse
  artifact (WB vs CC price for one guid), not repricing. New `price_disputed`
  flag in `path_c_merge.py` marks min/max >3x with both sources present; do
  not publish negotiation-margin claims until audited.
- **Capture bias (accepted):** cars per capture year fall 2,041 → 0 (2024),
  whole months missing — volume tracks crawls, not demand.

Our own rerun (`scripts/market_tests.py`, mix-uncontrolled) corroborates the
direction: phone medians 15.5K (2022) → 17.5K (2023, +13% vs ~+18% FX slide);
2024+ drops are composition shifts, not deflation. Event-week volumes on mined
`first_seen` are INVALID for event studies (first_seen = first capture, tracks
crawls) — use live `date_created` instead.

## A humility note

Jiji publishes from complete internal numbers; we work from an archival
sample with the gaps listed above and below. Where we disagree with their
figures, they are right and we are sampling noise. This dataset's value is
longitudinal structure (prices + time + place for research), not census truth.

## 2026 holdings (2026-10-14)

Live `data/jiji.db`: **43,826 listings** (runs since 09-27), 701,119-row mined
snapshot (`jiji_mined_dataset_20261014_100824` + KE-only slice — first with
`url`/`unverified`/`price_disputed`), 111,422 warc_item rows with **0 real
blank titles**, 633 sellers scored, 1,848 listing-level flags, 26 land areas.
CRSP age-banding live on the fresh snapshot (n=2,895): asking runs 36–63% of
KRA-depreciated new value in the 3–11y bands. Round-2 history drain (9,659
urls, 19,318 captures) dispatched to fleet.

## Time-history depth (2026-10-13: reservoir tapped)

Live `price_history` compounds slowly (43k rows, ~260 multi-obs) because
listings churn faster than repricing. But the manifest holds ~60k repeat
captures (8.5k urls × 3+, 2.8k × 5+) that the drain discarded via
INSERT OR REPLACE. New `scripts/warc_history.py` replays any URL's full
capture sequence into append-only `warc_history(url, capture_ts, ...)`:
first 12 cars show real trajectories (e.g. 4.10M → 3.85M over 2020–2021)
plus widespread sticky asking prices (flat for years — itself a finding).
`--guid` mode does one listing. Scale-up: fleet job over all multi-capture
item urls (category pages excluded — they carry no prices).

## Backlog (honest)

- 2024 WARC blank titles: CLOSED 2026-10-12 (28,612 → 0 real listings).
  Offline replay fixed 27,253; manifest shard re-fetch (+853, `warc_blank.db`
  merged) + URL-slug fallback covered the rest. Final 342 blanks are
  login/about wrapper URLs, not listings. Third fallback layer + `--repair`
  mode documented in `backfill_titles.py`; extractor also fixed forward
  (og:title → `<title>` peel → slug derive).
- `seller_risk` live (633 sellers) AND `listing_risk` live (1,848 flagged) —
  the listing-level table catches seller-hidden bait (Henry Phones pattern)
  that seller aggregates structurally miss. `fraud_watch` (new) auto-flags
  impossible-price + hidden-seller + 50-view rows as a scheduled query
  (leases excluded — different market). Ingest-time enrichment
  (`--enrich-new-max 30`, 2.5s apart) closes the seller gap at the source.
  `live-home-runner.yml` refreshes risk tables in its weekly hygiene slot
  (`data_quality.py --days 14` for died_at + backfills).
- `views_history` now records per-enrichment view counts (velocity =
  views-without-sale detection, our best trust signal).
- FX: `data/fx_rates.csv` now OFFICIAL World Bank annuals (PA.NUS.FCRF +
  FP.CPI.TOTL 2019–2025; 2026 actual 129.78 via er-api) — the approx anchors
  are retired (2024 was wrong: 129.0 vs actual 134.8). `market_tests.py`
  prints CPI-deflated (2019 KES) medians. Monthly IMF join attempted 2026-10-12
  (dataservices.imf.org unresolvable from here; exact query documented in
  session memory) — annual is sufficient for yearly pass-through work.
- Land: location-aware per-sqm engine live (`land_price.py` + `land_areas`
  medians: Kilimani 109K → Eldoret 182/sqm); coordinates absent from schema,
  text mapping carries it.
- `attrs_json` (mileage/engine) exists only for 2025; live-side equivalent now
  exists: `scripts/attrs_extract.py` back-extracts mileage_km/engine_cc (cars)
  and land_unit (per-acre/per-plot/per-sqm) from text into `listings`, wired
  into `item.py` enrich going forward. Measured 2026-10-11: 502 engines, 83
  mileages (sellers write "genuine mileage" without numbers — text ceiling),
  677 land units.
- Jobs-as-labour-signal measured 2026-10-11 and parked: 302 job rows, ~0 with
  any pay figure in price OR text. No signal exists to extract.
- Land/house-sale per-plot vs per-acre unit: DONE via land_unit above.
- Nigeria/Ghana/Tanzania rows (~4k) too few to use — kept for FX comparison only.
- `seller_risk` live (633 sellers, 130 flagged 2026-10-11) with known blind
  spots: sellerless rows invisible; heterogeneous categories inflate scores.
  Henry Phones never appears (hidden seller) — impossible-price + hidden-seller
  is itself the top-risk tier.

## Ops

- Secrets: `.secrets/github_token` (gitignored, owner-only ACL). Read with
  `Get-Content .secrets/github_token -Raw`, trim, clear the variable after use.
  Validated 5000 req/hr core + 30/min search.
- GitHub Actions = scheduler/CI only. Never scrape from runners (shared Azure
  IPs get flagged; bursts = detection).
- Cron: daily incremental 03:00 EAT, weekly full verify, monthly frozen
  `jiji_mined_dataset_YYYYMMDD` snapshot + tag.
- Stealth rules: 1–2 workers max, Nairobi hours, `curl_cffi`-grade TLS
  fingerprints if gated, `sort=new` + sitemap diff (deltas, never full sweeps),
  Jiji `robots.txt` allows listings/categories/sellers (re-check ToS before
  commercializing; price facts = research posture, don't republish photos/text).
