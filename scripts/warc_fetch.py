"""Fetch Common Crawl WARC bodies for Jiji item pages and extract structured fields.

Recovers the fields the mined history lacks: condition, seller (+seller page,
verified badge), category/region, price, full attribute pairs.

Usage:
  python scripts/warc_fetch.py --max 2000 --delay 1.0

Resume-safe: fetched URLs live in data/jiji.db:warc_item (url PK). Each run
picks the latest CC capture per not-yet-fetched item URL and works the list.
Polite by design: single worker, ~1s delay, byte-range requests only.
"""
import argparse
import gzip
import io
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import requests

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
MANIFEST = BASE + "/cc_manifest/manifest.parquet"
DB = BASE + "/data/jiji.db"
CC = "https://data.commoncrawl.org/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jiji-research-enrich/1.0 (+contact: research)"}

PRICE_CAP = 1_000_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS warc_item(
  url TEXT PRIMARY KEY, capture_ts TEXT, title TEXT, price INTEGER,
  condition TEXT, seller TEXT, seller_page TEXT, verified INTEGER,
  category TEXT, region TEXT, attrs_json TEXT, fetched_at TEXT,
  description TEXT);
"""

WARC_COLS = ("url,capture_ts,title,price,condition,seller,seller_page,"
             "verified,category,region,attrs_json,fetched_at,description")
WARC_Q = "?,?,?,?,?,?,?,?,?,?,?,?,?"

RE_TITLE = re.compile(r'<meta property="og:title" content="([^"]+)"')
RE_TITLE_TAG = re.compile(r"<title>(.*?)</title>", re.S)
RE_PRICE = re.compile(r'itemprop="price" content="(\d+)"')
RE_COND = re.compile(r'itemprop="itemCondition"><!--\[-->(.*?)<!--\]-->')
RE_ATTR = re.compile(
    r'b-advert-attribute__value"[^>]*><!--\[-->(.*?)<!--\]-->.*?'
    r'b-advert-attribute__key">(.*?)</div>', re.S)
RE_SELLER = re.compile(r'b-seller-block__name">(.*?)</div>')
RE_SELLER_PAGE = re.compile(r'href="(/sellerpage-[A-Za-z0-9]+)"')
RE_CRUMB = re.compile(r'b-breadcrumb-link"><span>(.*?)</span>')
# era-2 fallback (pre-2023 template): OpenGraph product tags + meta description
RE_OG_PRICE = re.compile(r'product:price:amount" content="([\d.]+)"')
RE_OG_COND = re.compile(r'product:condition" content="([^"]+)"')
RE_OG_CAT = re.compile(r'product:brand" content="([^"]+)"')
RE_META_DESC = re.compile(r'<meta name="description" content="([^"]{20,2000})"')


def clean(s):
    return re.sub(r"\s+", " ", s.replace("&amp;", "&")).strip()


def extract(html, url):
    m = RE_TITLE.search(html)
    title = clean(m.group(1)) if m else ""
    m = RE_PRICE.search(html)
    price = None
    if m:
        try:
            p = int(m.group(1))
            price = p if 0 < p <= PRICE_CAP else None
        except (TypeError, ValueError):
            price = None
    m = RE_COND.search(html)
    condition = clean(m.group(1)) if m else ""
    attrs = {}
    for v, k in RE_ATTR.findall(html):
        attrs[clean(k)] = clean(v)
    m = RE_SELLER.search(html)
    seller = clean(m.group(1)) if m else ""
    m = RE_SELLER_PAGE.search(html)
    seller_page = m.group(1) if m else ""
    if not seller:
        mt = RE_TITLE_TAG.search(html)
        if mt:
            t = clean(mt.group(1))
            if " | Jiji" in t and ", " in t.rsplit(" | Jiji", 1)[0]:
                seller = t.rsplit(" | Jiji", 1)[0].rsplit(", ", 1)[-1]
    crumbs = [clean(c) for c in RE_CRUMB.findall(html)]
    links = [c for c in crumbs if c != "All ads"]
    # last crumb is the item title itself; category = the one before it
    category = links[-2] if len(links) >= 2 else (links[-1] if links else "")
    region = ""
    mt = RE_TITLE_TAG.search(html)
    if mt:
        t = clean(mt.group(1))
        mm = re.search(r" in (.+?) \| Jiji", t)
        if mm and " - " in mm.group(1):
            region = mm.group(1).rsplit(" - ", 1)[0]
    # era-2 fallback: OG product tags predate the itemprop markup
    if price is None:
        m = RE_OG_PRICE.search(html)
        if m:
            try:
                p = int(float(m.group(1)))
                price = p if 0 < p <= PRICE_CAP else None
            except (TypeError, ValueError):
                pass
    if not condition:
        m = RE_OG_COND.search(html)
        if m:
            condition = clean(m.group(1))
    if not category:
        m = RE_OG_CAT.search(html)
        if m:
            category = clean(m.group(1))
    m = RE_META_DESC.search(html)
    meta_desc = clean(m.group(1)) if m else ""
    return {
        "title": title, "price": price, "condition": condition,
        "seller": seller, "seller_page": seller_page,
        "verified": 1 if "Verified ID" in html else 0,
        "category": category or attrs.get("Category", ""), "region": region,
        "attrs_json": json.dumps(attrs, ensure_ascii=False),
        "description": meta_desc,
    }


import queue
import threading
from concurrent.futures import ThreadPoolExecutor

_tls = threading.local()


def get_session():
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(UA)
        ad = requests.adapters.HTTPAdapter(pool_connections=4,
                                           pool_maxsize=4,
                                           max_retries=0)
        s.mount("https://", ad)
        _tls.s = s
    return s


class Throttle:
    """Adaptive rate limit: fast start, multiplicative backoff on
    429/503/timeout, slow recovery. Shared across workers."""
    def __init__(self, delay, lo=0.05, hi=10.0):
        self.delay = delay
        self.lo, self.hi = lo, hi
        self.lock = threading.Lock()
        self.ok_streak = 0
        self.events = {"ok": 0, "throttled": 0, "error": 0}

    def wait(self):
        import random
        with self.lock:
            d = self.delay
        time.sleep(d * random.uniform(0.7, 1.3))

    def good(self):
        with self.lock:
            self.events["ok"] += 1
            self.ok_streak += 1
            if self.ok_streak >= 25:
                self.delay = max(self.delay * 0.85, self.lo)
                self.ok_streak = 0

    def bad(self, throttled):
        with self.lock:
            self.events["throttled" if throttled else "error"] += 1
            self.ok_streak = 0
            self.delay = min(self.delay * (2.0 if throttled else 1.3),
                             self.hi)


HOT = ("computer", "laptop", "phone", "tablet", "vehicle", "car", "camera",
       "electronic", "audio", "videogame", "furniture")


def priority_key(url):
    low = url.lower()
    return (0 if any(h in low for h in HOT) else 1, url)


def fetch_body_v2(fn, off, ln, thr):
    """Returns (status, body): status in ok/retry/fail."""
    try:
        r = get_session().get(CC + fn,
                              headers={"Range": f"bytes={off}-{off+ln-1}"},
                              timeout=60)
    except requests.RequestException:
        thr.bad(False)
        return "fail", None
    if r.status_code == 429 or r.status_code in (502, 503, 504):
        thr.bad(True)
        return "retry", None
    if r.status_code not in (200, 206):
        thr.bad(False)
        return "fail", None
    try:
        raw = gzip.GzipFile(fileobj=io.BytesIO(r.content)).read()
    except Exception:
        thr.bad(False)
        return "fail", None
    parts = raw.split(b"\r\n\r\n")
    if len(parts) < 3:
        thr.bad(False)
        return "fail", None
    thr.good()
    return "ok", parts[2].decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=2000)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--batch", type=int, default=250)
    ap.add_argument("--shard-file", default=None,
                    help="TSV worklist (url,ts,file,off,len) for runners; "
                         "skips manifest+state, writes --out-db")
    ap.add_argument("--out-db", default=None,
                    help="output sqlite for shard mode (default: main DB)")
    ap.add_argument("--refetch-empty", action="store_true",
                    help="reprocess fetched rows missing condition+category "
                         "(e.g. after extractor upgrades)")
    args = ap.parse_args()

    out_db = args.out_db or DB
    db = sqlite3.connect(out_db, timeout=120, isolation_level=None,
                         check_same_thread=False)
    db_lock = threading.Lock()
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA busy_timeout=120000;")
    db.executescript(SCHEMA)
    cols = [c[1] for c in db.execute("PRAGMA table_info(warc_item)").fetchall()]
    if "description" not in cols:
        db.execute("ALTER TABLE warc_item ADD COLUMN description TEXT")
        print("migrated warc_item: +description")

    if args.shard_file:
        # runner mode: self-contained worklist, no manifest, no state
        todo = []
        with open(args.shard_file, encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) == 5:
                    todo.append((p[0], p[1], p[2], int(p[3]), int(p[4])))
        print(f"shard: {len(todo)} urls", flush=True)
    else:
        import duckdb
        con = duckdb.connect()
        # latest capture per item url (file/offset/length included)
        rows = con.execute(f"""SELECT url, capture_ts, warc_filename,
            warc_record_offset, warc_record_length FROM '{MANIFEST}'
            WHERE mime='text/html' AND url LIKE '%.html%'
            QUALIFY capture_ts = max(capture_ts) OVER (PARTITION BY url)""").fetchall()
        con.close()
        print(f"manifest: {len(rows)} distinct item urls", flush=True)
        have = {r[0] for r in db.execute("SELECT url FROM warc_item").fetchall()}
        if args.refetch_empty:
            empt = {r[0] for r in db.execute(
                "SELECT url FROM warc_item "
                "WHERE (condition IS NULL OR condition='') "
                "AND (category IS NULL OR category='')").fetchall()}
            have -= empt
            print(f"refetch-empty: {len(empt)} rows queued for re-extraction")
        todo = sorted((r for r in rows if r[0] not in have),
                      key=lambda r: priority_key(r[0]))
        print(f"already fetched: {len(have)}, todo: {len(todo)}", flush=True)
    todo = todo[:args.max] if args.max else todo

    thr = Throttle(args.delay)
    in_q = queue.Queue()
    for job in todo:
        in_q.put(job)
    out_q = queue.Queue()
    failed = []
    failed_lock = threading.Lock()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stop = threading.Event()
    stats = {"done": 0, "enriched": 0, "retry": 0}
    t0 = time.time()

    def worker():
        while not stop.is_set():
            try:
                u, ts, fn, off, ln = in_q.get_nowait()
            except queue.Empty:
                return
            thr.wait()
            st, body = fetch_body_v2(fn, off, ln, thr)
            if st == "retry":
                stats["retry"] += 1
                time.sleep(5)
                st, body = fetch_body_v2(fn, off, ln, thr)
            if st != "ok" or not body:
                with failed_lock:
                    failed.append((u, ts, fn, off, ln))
                in_q.task_done()
                continue
            try:
                d = extract(body, u)
            except Exception:
                in_q.task_done()
                continue
            out_q.put((u, ts, d["title"], d["price"], d["condition"],
                       d["seller"], d["seller_page"], d["verified"],
                       d["category"], d["region"], d["attrs_json"],
                       d["description"], now))
            in_q.task_done()

    def writer():
        buf = []

        def flush():
            with db_lock:
                db.executemany(
                    f"INSERT OR REPLACE INTO warc_item({WARC_COLS}) "
                    f"VALUES({WARC_Q})",
                    [(u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7], u[8],
                      u[9], u[10], now, u[11]) for u in buf])
                db.commit()
            stats["done"] += len(buf)
            el2 = time.time() - t0
            print(f"  {stats['done']} rows, delay={thr.delay:.2f}s, "
                  f"throttle={thr.events}, "
                  f"{stats['done']/max(el2,1):.1f} rows/s", flush=True)
            buf.clear()

        while not (stop.is_set() and out_q.empty()):
            try:
                buf.append(out_q.get(timeout=1))
            except queue.Empty:
                continue
            if len(buf) >= args.batch:
                flush()
        if buf:
            flush()

    wt = threading.Thread(target=writer, daemon=True)
    wt.start()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for _ in range(args.workers):
            ex.submit(worker)
    in_q.join()
    stop.set()
    wt.join()
    el = time.time() - t0
    # failures persist next to the output DB: re-running the same shard
    # retries exactly these (they're absent from out-db). Nothing is lost
    # when a worker dies — committed batches + out-db + this file survive.
    fail_path = out_db + ".failed.tsv"
    if failed:
        with open(fail_path, "w", encoding="utf-8") as f:
            for u, ts, fn, off, ln in failed:
                f.write(f"{u}\t{ts}\t{fn}\t{off}\t{ln}\n")
    print(f"done: fetched={stats['done']} failed={len(failed)} "
          f"retries={stats['retry']} "
          f"throttle_events={thr.events} delay={thr.delay:.2f}s "
          f"{stats['done']/max(el,1):.1f} rows/s in {el:.0f}s", flush=True)


if __name__ == "__main__":
    main()
