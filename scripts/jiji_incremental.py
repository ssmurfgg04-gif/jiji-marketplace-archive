"""Layer 2: polite incremental collector for Jiji api_web JSON listings.

Usage:
  python scripts/jiji_incremental.py --query "ddr3 desktop ram" --query "ddr4 desktop ram" \\
      --slug computer-hardware --sort new --max-pages 3
  python scripts/jiji_incremental.py --query "ddr3 desktop ram" --sort price --max-pages 2

One worker, random 3-8s delays, retries with backoff. Stores SQLite
(data/jiji.db) + daily Parquet snapshot (data/snapshot-YYYY-MM-DD.parquet).
BLAKE3 change detection: only changed records touch price_history.
"""
import argparse
import json
import random
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import blake3
import duckdb

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jiji-snapshot/1.0"}
BASE = "https://jiji.co.ke/api_web/v1/listing"
PER_PAGE = 20


def fetch(url, tries=3):
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # dead category / bad slug: don't retry, don't kill the sweep
            if e.code in (400, 403, 404, 410):
                return {"_dead": True, "code": e.code}
            last = e
            time.sleep(2 * (a + 1))
        except Exception as e:  # noqa: BLE001 - network is flaky, retry all
            last = e
            time.sleep(2 * (a + 1))
    raise RuntimeError(f"fetch failed {url}: {last}")


def condition_of(ad):
    for attr in ad.get("attrs") or []:
        if isinstance(attr, dict) and attr.get("name") == "Condition":
            return attr.get("value")
    return None


def norm(ad, query):
    raw = (ad.get("price_obj") or {}).get("value")
    try:
        price = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        price = None
    # price-sanity filter: reject true garbage only — zero/negatives and
    # billion-plus parse artifacts (phone numbers leaking into price).
    # NULL (no-price/contact listings) and genuine micro/high prices pass
    # through; analysts apply the 200-20M band at query time.
    if price is not None and (price <= 0 or price > 1_000_000_000):
        return None
    rec = {
        "guid": ad.get("guid") or str(ad.get("id")),
        "title": ad.get("title") or "",
        "price": price,
        "price_title": ad.get("price_title") or "",
        "url": "https://jiji.co.ke" + (ad.get("url") or "").split("?")[0],
        "user_id": ad.get("user_id"),
        "seller": (ad.get("seller") or {}).get("user_name"),
        "category": ad.get("category_name"),
        "slug": ad.get("category_slug"),
        "region": ad.get("region_name"),
        "region_slug": ad.get("region_slug"),
        "condition": condition_of(ad),
        "status": ad.get("status"),
        "query": query,
        # seller's own words: fault disclosures live here ("screen broken",
        # "not powering", "no battery"). Truncated in list payload; item.py
        # upgrades key rows to the full text on demand.
        "description": ad.get("short_description") or ad.get("details") or "",
    }
    canon = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    rec["hash"] = blake3.blake3(canon.encode("utf-8")).hexdigest()
    return rec


SCHEMA = """
CREATE TABLE IF NOT EXISTS listings(
  guid TEXT PRIMARY KEY, title TEXT, price INTEGER, price_title TEXT,
  url TEXT, user_id INTEGER, seller TEXT, category TEXT, slug TEXT,
  region TEXT, region_slug TEXT, condition TEXT, status TEXT,
  query TEXT, first_seen TEXT, last_seen TEXT, last_hash TEXT,
  description TEXT, description_full TEXT, date_created TEXT, views TEXT);
CREATE TABLE IF NOT EXISTS price_history(
  guid TEXT, seen_at TEXT, price INTEGER, hash TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, query TEXT,
  sort TEXT, pages INTEGER, new INTEGER,changed INTEGER);
-- per-slug totals for delta mode (skip deep pages when nothing moved)
CREATE TABLE IF NOT EXISTS slug_state(
  slug TEXT PRIMARY KEY, total INTEGER, updated TEXT);
-- fuzzy search index (mirror of scripts/fuzz.py FTS_SCHEMA; triggers keep it live)
CREATE VIRTUAL TABLE IF NOT EXISTS listings_fts USING fts5(
  guid UNINDEXED, title, description, tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS listings_ai AFTER INSERT ON listings BEGIN
  INSERT INTO listings_fts(guid, title, description)
  VALUES (new.guid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS listings_ad AFTER DELETE ON listings BEGIN
  DELETE FROM listings_fts WHERE guid = old.guid;
END;
CREATE TRIGGER IF NOT EXISTS listings_au AFTER UPDATE OF title, description ON listings BEGIN
  DELETE FROM listings_fts WHERE guid = old.guid;
  INSERT INTO listings_fts(guid, title, description)
  VALUES (new.guid, new.title, new.description);
END;
"""


def migrate(db):
    """Column migrations for existing DBs (CREATE TABLE IF NOT EXISTS
    never alters). Safe to run every startup."""
    cols = [c[1] for c in db.execute("PRAGMA table_info(listings)").fetchall()]
    for col in ("description", "description_full", "date_created", "views"):
        if col not in cols:
            db.execute(f"ALTER TABLE listings ADD COLUMN {col} TEXT")
            print(f"migrated: +{col}")
    fts_cols = [c[1] for c in db.execute("PRAGMA table_info(listings_fts)").fetchall()]
    if "description" not in fts_cols:
        db.execute("DROP TABLE IF EXISTS listings_fts")
        db.executescript("""
CREATE VIRTUAL TABLE listings_fts USING fts5(
  guid UNINDEXED, title, description, tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS listings_ai AFTER INSERT ON listings BEGIN
  INSERT INTO listings_fts(guid, title, description)
  VALUES (new.guid, new.title, new.description);
END;
CREATE TRIGGER IF NOT EXISTS listings_ad AFTER DELETE ON listings BEGIN
  DELETE FROM listings_fts WHERE guid = old.guid;
END;
CREATE TRIGGER IF NOT EXISTS listings_au AFTER UPDATE OF title, description ON listings BEGIN
  DELETE FROM listings_fts WHERE guid = old.guid;
  INSERT INTO listings_fts(guid, title, description)
  VALUES (new.guid, new.title, new.description);
END;""")
        db.execute("INSERT INTO listings_fts(guid, title, description) "
                   "SELECT guid, title, description FROM listings")
        print("migrated: fts rebuilt with description")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", action="append", default=[])
    ap.add_argument("--slugs-file",
                    help="file with one category slug per line; runs --query '' per slug for deep category mining")
    ap.add_argument("--slug", default="computer-hardware")
    ap.add_argument("--sort", default="new", choices=["new", "price", "rel"])
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--db", default="data/jiji.db")
    ap.add_argument("--dmin", type=float, default=3.0)
    ap.add_argument("--dmax", type=float, default=8.0)
    args = ap.parse_args()

    db = sqlite3.connect(args.db, timeout=120, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA busy_timeout=120000;")
    db.executescript(SCHEMA)
    migrate(db)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    jobs = [(q, args.slug) for q in args.query]
    if args.slugs_file:
        with open(args.slugs_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                s = line.split("\t")[0].strip()
                if s:
                    jobs.append(("", s))
    if not jobs:
        raise SystemExit("nothing to do: pass --query and/or --slugs-file")

    for query, slug in jobs:
        tag = f"slug:{slug}" if slug and not query else query
        new = changed = rejected = 0
        run = db.execute(
            "INSERT INTO runs(started_at,query,sort,pages,new,changed) VALUES(?,?,?,?,?,?)",
            (now, tag, args.sort, 0, 0, 0),
        ).lastrowid
        page, pages = 1, 0
        while page <= args.max_pages:
            q = {"slug": slug, "init_page": "true",
                 "webp": "true", "sort": args.sort, "page": str(page)}
            if query:
                q["query"] = query
            data = fetch(BASE + "?" + urllib.parse.urlencode(q))
            if data.get("_dead"):
                print(f"slug={slug} DEAD ({data.get('code')}), skipped")
                db.execute("UPDATE runs SET pages=?,new=?,changed=? WHERE id=?",
                           (0, new, changed, run))
                db.commit()
                break
            ads = (((data.get("adverts_list") or {}).get("adverts")) or [])
            if not ads:
                break
            pages += 1
            for ad in ads:
                r = norm(ad, query)
                if r is None:
                    rejected += 1  # price-sanity reject: never touches DB
                    continue
                done_ad = False
                for attempt in range(6):
                    db.execute("BEGIN")
                    try:
                        row = db.execute(
                            "SELECT price,last_hash FROM listings WHERE guid=?",
                            (r["guid"],)).fetchone()
                        if row is None:
                            db.execute(
                                "INSERT INTO listings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (r["guid"], r["title"], r["price"], r["price_title"], r["url"],
                                 r["user_id"], r["seller"], r["category"], r["slug"], r["region"],
                                 r["region_slug"], r["condition"], r["status"], r["query"],
                                 now, now, r["hash"], r["description"], None, None, None))
                            db.execute("INSERT INTO price_history VALUES(?,?,?,?)",
                                       (r["guid"], now, r["price"], r["hash"]))
                            new += 1
                        elif row[1] != r["hash"]:
                            db.execute(
                                "UPDATE listings SET title=?,price=?,price_title=?,url=?,user_id=?,"
                                "seller=?,category=?,slug=?,region=?,region_slug=?,condition=?,status=?,"
                                "query=?,last_seen=?,last_hash=?,description=? WHERE guid=?",
                                (r["title"], r["price"], r["price_title"], r["url"], r["user_id"],
                                 r["seller"], r["category"], r["slug"], r["region"], r["region_slug"],
                                 r["condition"], r["status"], r["query"], now, r["hash"],
                                 r["description"], r["guid"]))
                            if row[0] != r["price"]:
                                db.execute("INSERT INTO price_history VALUES(?,?,?,?)",
                                           (r["guid"], now, r["price"], r["hash"]))
                            changed += 1
                        else:
                            db.execute("UPDATE listings SET last_seen=? WHERE guid=?",
                                       (now, r["guid"]))
                    except sqlite3.OperationalError as e:
                        db.execute("ROLLBACK")
                        if "locked" in str(e).lower() and attempt < 5:
                            time.sleep(5 * (attempt + 1))
                            continue
                        raise
                    except Exception:
                        db.execute("ROLLBACK")
                        raise
                    else:
                        db.execute("COMMIT")
                        done_ad = True
                        break
                if not done_ad:
                    raise RuntimeError(f"locked out on {r['guid']}")
            total = (data.get("adverts_list") or {}).get("count", 0)
            if page * PER_PAGE >= total:
                break
            # delta mode (sort=new only): page 1 holds the newest ads. If it
            # yielded zero new rows AND the category total is unchanged since
            # the last run, deeper (older) pages cannot hold anything new.
            if args.sort == "new" and page == 1 and not query:
                prev = db.execute("SELECT total FROM slug_state WHERE slug=?",
                                  (slug,)).fetchone()
                if prev is not None and prev[0] == total:
                    page1_new = db.execute(
                        "SELECT COUNT(*) FROM listings WHERE slug=? AND "
                        "first_seen >= ?", (slug, now)).fetchone()[0]
                    if page1_new == 0:
                        print(f"slug={slug} delta-skip (total={total} unchanged)")
                        pages = max(pages, 1)
                        break
            page += 1
            time.sleep(random.uniform(args.dmin, args.dmax))
        if not query:
            db.execute("INSERT OR REPLACE INTO slug_state VALUES(?,?,?)",
                       (slug, total, now))
        db.execute("UPDATE runs SET pages=?,new=?,changed=? WHERE id=?", (pages, new, changed, run))
        db.commit()
        print(f"slug={slug} query={query!r} pages={pages} new={new} changed={changed} price_rejected={rejected}")

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM sqlite_scan('{args.db}', 'listings')) "
                f"TO 'data/snapshot-{day}.parquet' (FORMAT PARQUET)")
    n = con.execute(f"SELECT count(*) FROM 'data/snapshot-{day}.parquet'").fetchone()[0]
    print(f"snapshot data/snapshot-{day}.parquet rows={n}")
    print("cheapest 5 overall:")
    for t, p, u in con.execute(
            f"SELECT title, price, url FROM 'data/snapshot-{day}.parquet' "
            f"WHERE price IS NOT NULL ORDER BY price LIMIT 5").fetchall():
        print(f"  {p:>8,} | {t[:70]} | {u}")


if __name__ == "__main__":
    main()
