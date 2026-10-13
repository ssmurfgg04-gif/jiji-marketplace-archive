"""Fraud watch: Henry-pattern auto-flag (impossible price + hidden seller).

Henry Phones never appears in seller_risk (his DB rows hide the seller name),
so score what IS visible: price far below category median + no seller contact
+ real view counts (attention, no conversion). Paid-badge status is not in the
DB schema — views>=50 is the observable proxy for promoted bait.

Writes fraud_watch(guid, reason, checked_at). Rerun anytime; idempotent.
Intended as a scheduled query — run daily, alert on new guids.

Usage: python scripts/fraud_watch.py [--views 50] [--ratio 0.4]
"""
import argparse
import sqlite3
import statistics
from datetime import datetime, timezone

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views", type=int, default=50)
    ap.add_argument("--ratio", type=float, default=0.4)
    args = ap.parse_args()

    db = sqlite3.connect(DB, timeout=120)
    cats = {}
    for cat, price in db.execute(
            "SELECT category, price FROM listings WHERE price IS NOT NULL "
            "AND price > 0 AND price < 20000000 AND category IS NOT NULL"):
        cats.setdefault(cat, []).append(price)
    med = {c: statistics.median(v) for c, v in cats.items() if len(v) >= 10}

    db.execute("""CREATE TABLE IF NOT EXISTS fraud_watch(
      guid TEXT PRIMARY KEY, reason TEXT, checked_at TEXT)""")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = 0
    for guid, title, price, cat, views in db.execute(
            "SELECT guid, title, price, category, views FROM listings "
            "WHERE (seller IS NULL OR seller='') AND price IS NOT NULL AND price > 0"):
        blob = f"{title or ''}".lower()
        # leases/rents quote periodic prices — never comparable to sale medians
        if any(k in blob for k in ("leas", "rent", " to let", "to-let", "letting")):
            continue
        m = med.get(cat or "")
        if not m:
            continue
        if price / m >= args.ratio:
            continue
        v = int(views) if views and str(views).isdigit() else 0
        if v < args.views:
            continue
        reason = (f"impossible-price {price:,.0f} vs {cat} med {m:,.0f} "
                  f"({price/m*100:.0f}%), hidden seller, {v} views unsold")
        if not db.execute("SELECT 1 FROM fraud_watch WHERE guid=?", (guid,)).fetchone():
            db.execute("INSERT INTO fraud_watch VALUES(?,?,?)", (guid, reason, now))
            print(f"  ALERT {price:>10,} | {(title or '')[:60]} | {v} views")
            new += 1
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM fraud_watch").fetchone()[0]
    print(f"new alerts: {new}, watchlist total: {total}")
    db.close()


if __name__ == "__main__":
    main()
