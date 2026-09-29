# Jiji Kenya marketplace archive

An auditable, provenance-first snapshot of public Jiji marketplace listings for research and analysis. The repository contains the pipeline and documentation; immutable data snapshots are distributed as GitHub Release assets rather than normal Git objects.

## Release `v2026.09.29`

The first release contains three equivalent/complementary assets:

| Asset | Contents |
| --- | --- |
| `jiji.db` | SQLite catalogue: 12,494 live listings, 108,680 WARC-enriched items, 12,513 price observations, and 697 collection runs. |
| `jiji_mined_dataset_20260815_074727.parquet` | 485,830 historical marketplace records, columnar format. |
| `jiji_mined_dataset_20260815_074727.csv` | The same historical release in CSV form. |

The historical snapshot covers captures from 2019-07-16 through 2025-04-18. The live SQLite records span 2026-09-27 through 2026-09-29. The interval in between is an explicitly documented coverage gap; no synthetic records are introduced.

## Quick start

Download a release asset, then query the SQLite catalogue:

```sql
SELECT title, price, region, first_seen
FROM listings
WHERE suspicious IS NULL
ORDER BY last_seen DESC
LIMIT 20;
```

Or inspect the Parquet file with DuckDB:

```sql
SELECT category, count(*) AS listings
FROM 'jiji_mined_dataset_20260815_074727.parquet'
GROUP BY 1 ORDER BY 2 DESC;
```

## Repository layout

- `scripts/` — Common Crawl, Wayback, live collection, enrichment, normalization, and anomaly-detection tooling.
- `PIPELINE.md` — collection order, provenance, coverage limits, and operational notes.
- `schema/` — SQLite schema reference.

## Data quality and scope

The release has zero duplicate primary identifiers in both `listings` and `warc_item`. Category, seller, and region coverage is high for WARC records; condition and normalized-product coverage are intentionally reported as incomplete rather than inferred without evidence. See `PIPELINE.md` for the full coverage and provenance ledger.

Marketplace listings can change or disappear quickly. Treat prices, availability, seller information, and timestamps as historical observations—not current offers or endorsements. Use the data responsibly and review the source platform's terms before commercial redistribution.

## Reproducibility

The data-generation code is included for audit and controlled reruns. It relies on public web-archive sources and the source platform's published listing interfaces. Do not run collectors aggressively; preserve the documented rate limits and provenance fields.

## Release integrity

Each release includes a `SHA256SUMS.txt` file. Verify downloaded assets before analysis:

```powershell
Get-FileHash .\jiji.db -Algorithm SHA256
```
