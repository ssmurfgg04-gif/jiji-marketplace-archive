"""Offline title backfill for blank warc_item rows using local cc_bodies.

2026-10-11 finding: ~35% of 2024 item pages lack og:title (the extractor's
only title source) but all carry <title>. extract() now falls back to it;
this script replays that fallback over bodies already on disk — zero network.

Usage: python scripts/backfill_titles.py [--limit N]
Idempotent: only touches rows whose title is NULL/'', skips URLs with no
local body. Also fills empty region/category from the same parse.
"""
import argparse
import gzip
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from warc_fetch import extract

DB = os.path.join(os.path.dirname(BASE), "data", "jiji.db")
BODY_DIR = os.path.join(os.path.dirname(BASE), "cc_bodies")
META = os.path.join(os.path.dirname(BASE), "cc_meta.jsonl")


def load_body(h):
    p = os.path.join(BODY_DIR, h + ".gz")
    if not os.path.exists(p):
        return None
    try:
        raw = gzip.decompress(open(p, "rb").read())
    except Exception:
        return None
    m = raw.find(b"\r\n\r\n")
    payload = raw[m + 4:] if m >= 0 else raw
    if payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    return payload.decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repair", action="store_true",
                    help="recompute titles containing ',' where the fixed "
                         "extractor yields a longer title starting with the "
                         "stored one (repairs pre-fix over-trims)")
    args = ap.parse_args()

    url2hash = {}
    with open(META, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                h, url, date, country = json.loads(line)
            except Exception:
                continue
            url2hash.setdefault(url.split("?")[0], h)

    db = sqlite3.connect(DB, timeout=120)
    if args.repair:
        rows = db.execute(
            "SELECT url, title FROM warc_item WHERE title LIKE '%,%'").fetchall()
        print(f"repair candidates: {len(rows)}")
        n_rep = n_hit = 0
        for url, old in rows:
            if not url:
                continue
            h = url2hash.get(url.split("?")[0]) if 'url2hash' in dir() else None
            if not h:
                continue
            html = load_body(h)
            if not html:
                continue
            n_hit += 1
            try:
                rec = extract(html, url)
            except Exception:
                continue
            new = rec.get("title") or ""
            if new and len(new) > len(old) and new.startswith(old):
                db.execute("UPDATE warc_item SET title=? WHERE url=?", (new, url))
                n_rep += 1
            if (n_hit % 2000) == 0:
                db.commit()
        db.commit()
        print(f"done: bodies={n_hit} repaired={n_rep}")
        db.close()
        return
    rows = db.execute(
        "SELECT url FROM warc_item WHERE title IS NULL OR title=''").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"blank rows: {len(rows)}, meta urls: {len(url2hash)}")
    n_hit = n_fix = n_reg = 0
    for (url,) in rows:
        if not url:
            continue
        h = url2hash.get(url.split("?")[0])
        if not h:
            continue
        html = load_body(h)
        if not html:
            continue
        n_hit += 1
        try:
            rec = extract(html, url)
        except Exception:
            continue
        if rec.get("title"):
            db.execute("UPDATE warc_item SET title=? WHERE url=?", (rec["title"], url))
            n_fix += 1
        if rec.get("region"):
            db.execute("UPDATE warc_item SET region=? WHERE url=? AND (region IS NULL OR region='')",
                       (rec["region"], url))
        if rec.get("category"):
            db.execute("UPDATE warc_item SET category=? WHERE url=? AND (category IS NULL OR category='')",
                       (rec["category"], url))
        if (n_hit % 2000) == 0:
            db.commit()
            print(f"  bodies={n_hit} fixed={n_fix}", flush=True)
    db.commit()
    print(f"done: bodies_found={n_hit} titles_fixed={n_fix}")
    print("remaining blank:",
          db.execute("SELECT COUNT(*) FROM warc_item WHERE title IS NULL OR title=''").fetchone()[0])
    db.close()


if __name__ == "__main__":
    main()
