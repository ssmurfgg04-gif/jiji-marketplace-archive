"""Fault-flag scanner: never miss a seller's own disclosure.

Scans title + description for dead/faulty/missing-part language and prints
grouped hits with the matching snippet. The 840 G3 (broken screen) and 820 G3
(dead) would both have been caught here.

Usage:
  python scripts/flagged.py [--max-price 10000] [--limit 40]
  python scripts/flagged.py --query "laptop" --max-price 8000
"""
import argparse
import re
import sqlite3

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"

FAULT_PATTERNS = [
    # dead / not working
    "dead", "not working", "doesnt work", "doesn't work", "not powering",
    "wont turn on", "won't turn on", "no power", "failed", "faulty",
    # screens
    "broken screen", "cracked", "screen is broken", "no display", "lines on screen",
    "dot on the screen",
    # batteries / power
    "no battery", "battery is dead", "battery dead", "battery doesn", "needs battery",
    "works only on charging", "direct power",
    # parts / spares framing
    "for parts", "for spares", "as spares", "spare part", "sold as", "as-is",
    "as is", "as it is",
    # missing pieces
    "missing", "no ram", "no hdd", "no hard", "without battery", "without charger",
    "cpu only", "no keyboard",
    # repair needed
    "needs repair", "needs replacement", "needs fixing",
    "motherboard faulty", "motherboard dead", "board dead",
    "short circuit", "water damage",
]


def like_clause(patterns):
    where = " OR ".join(
        ["(lower(title) LIKE ? OR lower(coalesce(description,'')) LIKE ? "
         "OR lower(coalesce(description_full,'')) LIKE ?)"] * len(patterns))
    params = []
    for p in patterns:
        params += [f"%{p}%"] * 3
    return where, params


def snippet(text, patterns):
    low = (text or "").lower()
    for p in patterns:
        i = low.find(p)
        if i >= 0:
            s = max(0, i - 60)
            return "..." + " ".join(text[s:i + 80].split()) + "..."
    return ""


def scan(db, query=None, max_price=None, limit=40):
    where, params = like_clause(FAULT_PATTERNS)
    q = (f"SELECT guid, title, price, condition, seller, region, description, "
         f"description_full, url FROM listings WHERE ({where})")
    if query:
        q += " AND lower(title) LIKE ?"
        params.append(f"%{query.lower()}%")
    if max_price is not None:
        q += " AND price IS NOT NULL AND price <= ?"
        params.append(max_price)
    q += " ORDER BY price LIMIT ?"
    params.append(limit * 3)
    rows = db.execute(q, params).fetchall()
    out = []
    for guid, title, price, cond, seller, region, desc, full, url in rows:
        blob = f"{title} {desc or ''} {full or ''}"
        low = blob.lower()
        hits = sorted({p for p in FAULT_PATTERNS if p in low},
                      key=lambda p: low.find(p))
        if hits:
            out.append((hits[0], price, cond, seller, region, title, blob, url))
        if len(out) >= limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default=None)
    ap.add_argument("--max-price", type=int, default=None)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    db = sqlite3.connect(DB, timeout=120)
    hits = scan(db, args.query, args.max_price, args.limit)
    print(f"{len(hits)} flagged listings")
    for flag, price, cond, seller, region, title, blob, url in hits:
        p = f"{price:,}" if price is not None else "no-price"
        print(f"  [{flag}] {p} | {cond or '?'} | {seller or '?'} | {region or '?'} | {title[:60]}")
        print(f"       {snippet(blob, [flag])}")
        print(f"       {url}")


if __name__ == "__main__":
    main()
