"""Data-quality maintenance: died_at marking + category/region backfill.

- died_at: listings unseen for --days (default 14) get died_at = last_seen.
  Churn becomes explicit instead of implicit (fixes the missing sold flag
  as far as observable data allows: delisted, not sold).
- backfill: TRIM(category) (kills the 'Computer Accessories ' trailing-space
  dupe) + paren-strip regions ('Industrial Area (Nairobi)' -> 'Industrial
  Area Nairobi') to match norm_region() going forward.

Safe: additive column + whitespace-only updates. Idempotent.
Usage: python scripts/data_quality.py [--days 14]
"""
import argparse
import re
import sqlite3
from datetime import datetime, timezone

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    db = sqlite3.connect(DB, timeout=120)
    cols = [c[1] for c in db.execute("PRAGMA table_info(listings)").fetchall()]
    if "died_at" not in cols:
        db.execute("ALTER TABLE listings ADD COLUMN died_at TEXT")
        print("migrated: +died_at")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = db.execute(
        "UPDATE listings SET died_at=COALESCE(died_at, last_seen) "
        "WHERE last_seen < datetime('now', ?) AND (died_at IS NULL OR died_at='')",
        (f"-{args.days} days",))
    print(f"marked died: {cur.rowcount}")

    cur = db.execute(
        "UPDATE listings SET category=TRIM(category) "
        "WHERE category != TRIM(category)")
    print(f"trimmed categories: {cur.rowcount}")

    # region paren-strip backfill (python-side for regex parity with norm_region)
    n = 0
    for guid, region in db.execute(
            "SELECT guid, region FROM listings WHERE region LIKE '%(%'"):
        fixed = re.sub(r"\s+", " ", region.replace("(", "").replace(")", "")).strip()
        if fixed != region:
            db.execute("UPDATE listings SET region=? WHERE guid=?", (fixed, guid))
            n += 1
    print(f"normalized regions: {n}")
    db.commit()
    # prove the dupe is gone
    dupes = db.execute(
        "SELECT COUNT(*) FROM (SELECT TRIM(category) c FROM listings "
        "GROUP BY c HAVING COUNT(DISTINCT category) > 1)").fetchone()[0]
    print(f"remaining trim-dupe groups: {dupes} (want 0)" if dupes == 0 else f"REMAINING: {dupes}")
    print("ran_at:", now)


if __name__ == "__main__":
    main()
