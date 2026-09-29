"""Layer 0 manifest: cc_index/*.jsonl (CDX records) -> cc_manifest/manifest.parquet.

DuckDB-ready fetch plan for cc_fetch workers: one row per capture with the
exact WARC byte range. Offline-safe (reads local jsonl only). Resumeable:
re-running overwrites the manifest for the requested domains.

Usage:
  python scripts/cc_layer0.py                       # all domains in cc_index/
  python scripts/cc_layer0.py --domain jiji.co.ke   # single domain
  python scripts/cc_layer0.py --min-year 2026       # recent captures only
"""
import argparse
import json
import os

import duckdb

BASE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(BASE, "..", "cc_index")
OUT = os.path.join(BASE, "..", "cc_manifest")


def load(domain, min_year):
    path = os.path.join(IDX, f"{domain}.jsonl")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = str(r.get("timestamp", ""))
            if min_year and ts[:4].isdigit() and int(ts[:4]) < min_year:
                continue
            if str(r.get("status")) != "200":
                continue
            rows.append({
                "url": r.get("url"),
                "capture_ts": ts,
                "crawl": r.get("_coll"),
                "warc_filename": r.get("filename"),
                "warc_record_offset": int(r.get("offset") or 0),
                "warc_record_length": int(r.get("length") or 0),
                "mime": r.get("mime"),
                "digest": r.get("digest"),
                "domain": domain,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--min-year", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    doms = [args.domain] if args.domain else [
        fn[:-6] for fn in sorted(os.listdir(IDX)) if fn.endswith(".jsonl")]
    all_rows, per = [], {}
    for d in doms:
        try:
            rows = load(d, args.min_year)
        except FileNotFoundError:
            print(f"skip {d}: no jsonl")
            continue
        all_rows.extend(rows)
        per[d] = len(rows)
        print(f"{d}: {len(rows)} captures")
    if not all_rows:
        print("nothing to write")
        return
    out = args.out or os.path.join(OUT, "manifest.parquet")
    tmp = os.path.join(OUT, "_manifest_tmp.jsonl")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r) + "\n")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM read_json('{tmp}')) TO '{out}' (FORMAT PARQUET)")
    os.remove(tmp)
    n = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
    print(f"wrote {out} rows={n} crawls=",
          con.execute(f"SELECT count(DISTINCT crawl) FROM '{out}'").fetchone()[0])


if __name__ == "__main__":
    main()
