"""Fuzzy search over the live Jiji listings.

Two-stage: SQLite FTS5 trigram index for fast candidate recall (typo- and
substring-tolerant: 'think pad', 't 480', 'samsug'), then rapidfuzz WRatio
rerank for honest scoring.

Setup (once, or auto on every incremental run):
  python scripts/fuzz.py --reindex

Usage:
  python scripts/fuzz.py "think pad t408" --limit 15
  python scripts/fuzz.py "laptop" --max-price 6000 --limit 20
  python scripts/fuzz.py "dell latitude" --min-score 65
"""
import argparse
import sqlite3
import sys

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"

FTS_SCHEMA = """
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


def ensure_fts(db):
    db.executescript(FTS_SCHEMA)
    db.commit()


def reindex(db):
    ensure_fts(db)
    db.execute("DELETE FROM listings_fts")
    db.execute("INSERT INTO listings_fts(guid, title, description) "
               "SELECT guid, title, description FROM listings")
    db.commit()
    n = db.execute("SELECT count(*) FROM listings_fts").fetchone()[0]
    print(f"reindexed {n} titles")


def search(db, query, limit=20, min_score=55, max_price=None):
    from rapidfuzz import fuzz, process
    words = [w.strip('"') for w in query.split() if len(w.strip('"')) >= 2]
    if not words:
        print("query too short")
        return []
    # trigram recall: OR the words (each word substring-matches via trigrams)
    match = " OR ".join(f'"{w}"' for w in words)
    try:
        cands = db.execute(
            "SELECT guid, title FROM listings_fts WHERE listings_fts MATCH ? "
            "LIMIT 2000", (match,)).fetchall()
    except sqlite3.OperationalError:
        cands = []
    if not cands:  # fall back to full scan for very short/odd queries
        cands = db.execute("SELECT guid, title FROM listings").fetchall()
    scored = process.extract(query, {g: t for g, t in cands},
                             scorer=fuzz.WRatio, limit=limit * 3)
    guids = [g for _, s, g in scored if s >= min_score][:limit * 2]
    if not guids:
        return []
    q = (f"SELECT guid, title, price, condition, seller, region, category, url "
         f"FROM listings WHERE guid IN ({','.join('?' * len(guids))})")
    params = list(guids)
    if max_price is not None:
        q += " AND price IS NOT NULL AND price <= ?"
        params.append(max_price)
    rows = {r[0]: r for r in db.execute(q, params).fetchall()}
    out = []
    for _, s, g in scored:
        if g in rows and s >= min_score:
            out.append((s,) + rows[g])
        if len(out) >= limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--min-score", type=float, default=55)
    ap.add_argument("--max-price", type=int, default=None)
    ap.add_argument("--reindex", action="store_true")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()

    db = sqlite3.connect(args.db, timeout=120)
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA busy_timeout=120000;")
    if args.reindex or not args.query:
        reindex(db)
        if not args.query:
            return
    else:
        ensure_fts(db)
    hits = search(db, args.query, args.limit, args.min_score, args.max_price)
    print(f"{len(hits)} hits for {args.query!r}")
    for h in hits:
        score, guid, title, price, cond, seller, region, cat, url = h
        p = f"{price:,}" if price is not None else "no-price"
        print(f"  [{score:.0f}] {p} | {cond or '?'} | {seller or '?'} | "
              f"{region or '?'} | {title[:65]}")
        print(f"       {url}")


if __name__ == "__main__":
    main()
