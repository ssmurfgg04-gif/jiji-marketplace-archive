"""Listing-level risk flags: catches bait without needing a seller name.

Henry Phones pattern hilariously defeats seller_risk (his DB rows hide the
seller). Score the listing: impossible price ratio + views-without-sale +
hidden-seller combo. Rerun anytime; idempotent.

Usage: python scripts/listing_risk.py
Writes: listing_risk(guid, price_ratio, views, seller_hidden, risk)
"""
import sqlite3
import statistics

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"


def main():
    db = sqlite3.connect(DB, timeout=120)
    cats = {}
    for cat, price in db.execute(
            "SELECT category, price FROM listings WHERE price IS NOT NULL "
            "AND price > 0 AND price < 20000000 AND category IS NOT NULL"):
        cats.setdefault(cat, []).append(price)
    med = {c: statistics.median(v) for c, v in cats.items() if len(v) >= 10}

    _old = db.execute("SELECT type FROM sqlite_master WHERE name='listing_risk'").fetchone()
    if _old:
        db.execute(f"DROP {_old[0].upper()} listing_risk")
    db.execute("""CREATE TABLE listing_risk(
      guid TEXT PRIMARY KEY, price_ratio REAL, views INTEGER,
      seller_hidden INTEGER, risk REAL)""")
    n = 0
    for guid, price, cat, views, seller in db.execute(
            "SELECT guid, price, category, views, seller FROM listings"):
        m = med.get(cat or "")
        ratio = (price / m) if (m and price) else None
        v = int(views) if views and str(views).isdigit() else 0
        hidden = 1 if not seller else 0
        risk = 0.0
        if ratio is not None and ratio < 0.40:
            risk += 2.0 * (1 - ratio / 0.40)
        if v >= 100:
            risk += 1.0
        if hidden and ratio is not None and ratio < 0.60:
            risk += 1.0
        risk = round(risk, 3)
        db.execute("INSERT INTO listing_risk VALUES(?,?,?,?,?)",
                   (guid, ratio, v, hidden, risk))
        if risk >= 2.0:
            n += 1
    db.commit()
    print(f"flagged (risk>=2.0): {n}")
    print("--- top ---")
    for guid, price, title, seller, rk in db.execute(
            """SELECT l.guid, l.price, l.title, l.seller, k.risk FROM listing_risk k
               JOIN listings l ON l.guid=k.guid ORDER BY k.risk DESC LIMIT 12"""):
        print(f"  {rk:.2f} | {price} | {(title or '')[:55]} | {seller or '<hidden>'}")
    db.close()


if __name__ == "__main__":
    main()
