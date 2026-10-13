"""Seller-risk scoring: turn today's manual detective work into a computed flag.

Henry Phones pattern: cluster of impossible-price listings + paid badge +
hundreds of views with zero sales. Samuel M pattern: new account + all-premium
catalog at uniform ~60% of market + title/detail contradictions.

Signals available in data/jiji.db (no new fetches):
  - impossible_share: fraction of seller's priced listings under 40% of
    category median (same band as deal_scan.py)
  - watched_unsold_share: fraction with views >= 100 (attention, no conversion)
  - catalog_size: listings count (new-account + big catalog = riskier)

score = 2*impossible_share + 1*watched_unsold_share (+0.5 if catalog>=10
and account looks new). Writes seller_risk(seller, n, impossible_share,
watched_unsold_share, score). Rerun anytime; idempotent.

Usage: python scripts/seller_risk.py [--min-listings 3]
"""
import argparse
import sqlite3
import statistics

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-listings", type=int, default=3)
    args = ap.parse_args()

    db = sqlite3.connect(DB, timeout=120)
    cats = {}
    for cat, price in db.execute(
            "SELECT category, price FROM listings WHERE price IS NOT NULL "
            "AND price > 0 AND price < 20000000 AND category IS NOT NULL"):
        cats.setdefault(cat, []).append(price)
    med = {c: statistics.median(v) for c, v in cats.items() if len(v) >= 10}

    db.execute("DROP TABLE IF EXISTS seller_risk")
    db.execute("""CREATE TABLE seller_risk(
      seller TEXT PRIMARY KEY, n INTEGER, impossible_share REAL,
      watched_unsold_share REAL, score REAL)""")

    sellers = [r[0] for r in db.execute(
        "SELECT DISTINCT seller FROM listings WHERE seller IS NOT NULL AND seller != ''")]
    n_flag = 0
    for s in sellers:
        rows = db.execute(
            "SELECT price, category, views FROM listings WHERE seller=?", (s,)).fetchall()
        if len(rows) < args.min_listings:
            continue
        priced = [(p, c) for p, c, v in rows if p and p > 0]
        imp = sum(1 for p, c in priced
                  if c in med and med[c] > 0 and p / med[c] < 0.40)
        watched = sum(1 for p, c, v in rows
                      if v and str(v).isdigit() and int(v) >= 100)
        n = len(rows)
        imp_s = imp / max(len(priced), 1)
        w_s = watched / n
        score = round(2 * imp_s + w_s + (0.5 if n >= 10 and imp_s > 0.3 else 0), 3)
        db.execute("INSERT INTO seller_risk VALUES(?,?,?,?,?)",
                   (s, n, round(imp_s, 3), round(w_s, 3), score))
        if score >= 1.0:
            n_flag += 1
    db.commit()
    print(f"sellers scored, {n_flag} at score>=1.0")
    print("--- top risk ---")
    for s, n, i, w, sc in db.execute(
            "SELECT * FROM seller_risk ORDER BY score DESC, n DESC LIMIT 15"):
        print(f"  {sc:.2f} | n={n:>3} imp={i:.0%} watched={w:.0%} | {s}")


if __name__ == "__main__":
    main()
