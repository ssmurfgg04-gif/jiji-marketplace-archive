"""Temporal depth backfill: fetch ALL captures for high-value URLs.

The drain keeps only the latest capture per URL (INSERT OR REPLACE), so the
~60k repeat captures in cc_manifest (8.5k urls with 3+, 2.8k with 5+) are
unexploited history. This script replays them into warc_history(url,
capture_ts, price, title, condition, seller, category, region, fetched_at) —
append-only, PK (url, capture_ts). That is the time-history depth the live
collector needs months to regrow.

Usage:
  python scripts/warc_history.py --top-cars 10 --min-captures 5
  python scripts/warc_history.py --guid <guid>   # one listing, all captures
Polite: 1 worker, ~1s delays (CC byte ranges, same etiquette as warc_fetch).
"""
import argparse
import os
import sys
import time
import sqlite3
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from warc_fetch import extract, fetch_body_v2, Throttle  # noqa: E402

DB = os.path.join(os.path.dirname(BASE), "data", "jiji.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-cars", type=int, default=10)
    ap.add_argument("--min-captures", type=int, default=5)
    ap.add_argument("--guid", default=None)
    ap.add_argument("--url-file", default=None,
                    help="fleet mode: TSV with one URL per line; capture list "
                         "resolved from local manifest (home use)")
    ap.add_argument("--shard-file", default=None,
                    help="fleet mode: TSV rows url,ts,file,off,len (manifest-free)  # v2")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--out-db", default=None,
                    help="fleet mode: write to this sqlite file instead of data/jiji.db")
    args = ap.parse_args()

    _con = {"db": None}

    def _manifest():
        # lazy: shard-file mode never touches the manifest (runners lack duckdb)
        if _con["db"] is None:
            try:
                import duckdb as _duckdb
                _con["db"] = _duckdb.connect()
            except ImportError:
                raise RuntimeError("duckdb not installed; use --shard-file mode or install duckdb")
        return _con["db"]
    if args.url_file:
        with open(args.url_file, encoding="utf-8") as f:
            urls = [l.strip().split("\t")[0] for l in f
                    if l.strip() and not l.startswith("#")]
        print(f"shard urls: {len(urls)}")
    elif args.guid:
        db0 = sqlite3.connect(DB)
        row = db0.execute("SELECT url FROM listings WHERE guid=?", (args.guid,)).fetchone()
        if not row:
            row = db0.execute("SELECT url FROM warc_item WHERE url LIKE ?", ("%" + args.guid + "%",)).fetchone()
        db0.close()
        if not row:
            print("guid not found")
            return
        urls = [row[0]]
    else:
        # history lives on OLD urls (dead listings predate live ones): pick car
        # URLs straight from the manifest by slug. Skipped entirely in
        # --shard-file mode (fleet runners have no manifest).
        urls = []
        if not args.shard_file:
            urls = [r[0] for r in _manifest().execute(
            "SELECT url FROM 'cc_manifest/manifest.parquet' "
            "WHERE url LIKE '%/cars/%' AND url LIKE '%.html%' GROUP BY url HAVING COUNT(*)>= " +
            str(args.min_captures) + " LIMIT " + str(args.top_cars)).fetchall()]
        print(f"manifest car urls with {args.min_captures}+ captures: {len(urls)}")

    db = sqlite3.connect(args.out_db or DB, timeout=120)
    db.execute("""CREATE TABLE IF NOT EXISTS warc_history(
      url TEXT, capture_ts TEXT, price INTEGER, title TEXT, condition TEXT,
      seller TEXT, category TEXT, region TEXT, fetched_at TEXT,
      PRIMARY KEY (url, capture_ts))""")
    thr = Throttle(args.delay)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_new = 0
    if args.shard_file:
        jobs = []
        with open(args.shard_file, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 5 and not line.startswith("#"):
                    jobs.append((parts[0], parts[1], parts[2], parts[3], parts[4]))
        print(f"shard jobs: {len(jobs)}")
        for i, (u, ts, fn, off, ln) in enumerate(jobs):
            if db.execute("SELECT 1 FROM warc_history WHERE url=? AND capture_ts=?",
                          (u, ts)).fetchone():
                continue
            thr.wait()
            st, body = fetch_body_v2(fn, int(off), int(ln), thr)
            if st != "ok" or not body:
                continue
            try:
                d = extract(body, u)
            except Exception:
                continue
            db.execute("INSERT OR IGNORE INTO warc_history VALUES(?,?,?,?,?,?,?,?,?)",
                       (u, ts, d.get("price"), d.get("title"), d.get("condition"),
                        d.get("seller"), d.get("category"), d.get("region"), now))
            n_new += 1
            if n_new % 100 == 0:
                db.commit()
                print(f"  {n_new} rows", flush=True)
        db.commit()
        print(f"new history rows: {n_new}")
        db.close()
        return
    for u in urls:
        caps = _manifest().execute(
            "SELECT capture_ts, warc_filename, warc_record_offset, warc_record_length "
            "FROM 'cc_manifest/manifest.parquet' WHERE url=? ORDER BY capture_ts", [u]).fetchall()
        for ts, fn, off, ln in caps:
            if db.execute("SELECT 1 FROM warc_history WHERE url=? AND capture_ts=?",
                          (u, ts)).fetchone():
                continue
            thr.wait()
            st, body = fetch_body_v2(fn, off, ln, thr)
            if st != "ok" or not body:
                continue
            try:
                d = extract(body, u)
            except Exception:
                continue
            db.execute("INSERT OR IGNORE INTO warc_history VALUES(?,?,?,?,?,?,?,?,?)",
                       (u, ts, d.get("price"), d.get("title"), d.get("condition"),
                        d.get("seller"), d.get("category"), d.get("region"), now))
            db.commit()
            n_new += 1
        traj = db.execute("SELECT capture_ts, price FROM warc_history WHERE url=? ORDER BY capture_ts",
                          (u,)).fetchall()
        print(f"{u[-30:]}: {' -> '.join(f'{t[:7]}:{p}' for t, p in traj)}")
    print(f"new history rows: {n_new}")
    print("warc_history total:",
          db.execute("SELECT COUNT(*) FROM warc_history").fetchone()[0])
    db.close()


if __name__ == "__main__":
    main()
