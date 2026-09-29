"""Merge runner shard outputs into the main warc_item table.

1. Download artifacts:  gh run download <run-id> -D data/warc_parts
   (or unzip them into that dir by hand)
2. python scripts/warc_merge.py

Attaches every warc_part_*.db and INSERT OR REPLACEs into data/jiji.db.
Idempotent: rerun any time.
"""
import glob
import os
import sqlite3

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
PARTS = BASE + "/data/warc_parts"
DB = BASE + "/data/jiji.db"


def main():
    files = sorted(glob.glob(PARTS + "/**/warc_part_*.db", recursive=True))
    if not files:
        files = sorted(glob.glob(PARTS + "/warc_part_*.db"))
    print(f"parts: {len(files)}")
    if not files:
        raise SystemExit(f"nothing to merge in {PARTS}")
    db = sqlite3.connect(DB, timeout=300, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA busy_timeout=300000;")
    total = 0
    for i, f in enumerate(files):
        db.execute(f"ATTACH '{f}' AS p{i}")
        # explicit columns: part DBs (fresh schema) and main DB (migrated)
        # disagree on description/fetched_at positions — never SELECT *.
        cols = ("url,capture_ts,title,price,condition,seller,seller_page,"
                "verified,category,region,attrs_json,fetched_at,description,"
                "condition_inferred")
        try:
            pcols = [c[1] for c in db.execute(f"PRAGMA p{i}.table_info(warc_item)").fetchall()]
        except Exception as e:
            print(f"  {os.path.basename(f)}: attach failed ({str(e)[:60]}), skipped")
            continue
        if "condition_inferred" not in pcols:
            sel = cols.replace(",condition_inferred", "") + ",NULL"
        else:
            sel = cols
        n = db.execute(
            f"INSERT OR REPLACE INTO warc_item({cols}) SELECT {sel} FROM p{i}.warc_item").rowcount
        db.execute(f"DETACH p{i}")
        total += n if n and n > 0 else 0
        print(f"  {os.path.basename(f)}: +{n}")
    print(f"merged ~{total} rows; warc_item now "
          f"{db.execute('SELECT count(*) FROM warc_item').fetchone()[0]}")


if __name__ == "__main__":
    main()
