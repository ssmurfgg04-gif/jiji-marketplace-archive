#!/usr/bin/env python3
"""PATH A Stage 1: Common Crawl index collector for jiji regional domains.

Queries index.commoncrawl.org CDXJ across every crawl (2014-2026) for the
regional domains, paginates with pageSize, and appends raw records to a local
JSONL cache (resumeable - already-fetched crawls are skipped).

Output: cc_index/<domain>.jsonl  (raw CDXJ records with filename/offset/length)
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.__file__))  # stdlib first (local inspect.py shadow)
import inspect  # cache stdlib inspect in sys.modules BEFORE requests/typing lazy-imports it
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cc_index")
os.makedirs(DATA_DIR, exist_ok=True)

DOMAINS = ["jiji.co.ke", "jiji.ng", "jiji.co.tz", "jiji.co.ug", "jiji.co.za",
           "jiji.et", "jiji.com.gh"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) research-archive-miner/2.0"}
SESSION = requests.Session()

def get_json(coll, domain, page, page_size=1500, retries=6):
    url = f"https://index.commoncrawl.org/{coll}-index"
    params = {"url": f"*.{domain}/", "output": "json", "page": page,
              "pageSize": page_size, "filter": "=status:200"}
    last = None
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 200:
                recs = []
                for line in r.text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(j, dict) and j.get("filename"):
                        recs.append(j)
                return recs
            last = r.status_code
            if r.status_code == 429:
                time.sleep(10)
            elif r.status_code in (502, 503, 504):
                time.sleep(2 + i * 3)
            else:
                # 404 = no captures
                return None if r.status_code == 404 else []
        except requests.RequestException as e:
            last = e
            time.sleep(2 + i * 4)
    print(f"  !! {coll} {domain} page {page} failed: {last}", file=sys.stderr)
    return []

def collect(domain, colls):
    out = os.path.join(DATA_DIR, f"{domain}.jsonl")
    for coll in colls:
        # dedupe guard: has this crawl already been saved for this domain?
        rows_this = 0
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                rows_this = sum(1 for l in f if l.strip() and f'"{coll}"' in l)
        if rows_this > 0:
            print(f"  {domain} {coll}: cached ({rows_this} rows), skip")
            continue
        page = 0
        batch = []
        while True:
            recs = get_json(coll, domain, page)
            if recs is None:
                break
            if not recs:
                break
            batch.extend(recs)
            if len(recs) < 1500:
                break
            page += 1
            if page > 60:
                break
            time.sleep(0.4)
        if batch:
            with open(out, "a", encoding="utf-8") as f:
                for j in batch:
                    j["_coll"] = coll
                    f.write(json.dumps(j, separators=(",", ":")) + "\n")
            print(f"  {domain} {coll}: +{len(batch)} rows")
        else:
            print(f"  {domain} {coll}: 0 rows")
        time.sleep(0.4)

def main():
    try:
        colls = [c["id"] for c in
                 requests.get("https://index.commoncrawl.org/collinfo.json", timeout=60).json()]
    except requests.RequestException as e:
        print(f"!! collinfo failed: {e}", file=sys.stderr)
        return 1
    target = [sys.argv[1]] if len(sys.argv) > 1 else DOMAINS
    for d in target:
        print(f"== {d} ==", flush=True)
        try:
            collect(d, colls)
        except Exception as e:
            print(f"!! {d} crashed: {e}", file=sys.stderr)
    print("DONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())