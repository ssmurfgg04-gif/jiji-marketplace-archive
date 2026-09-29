#!/usr/bin/env python3
"""PATH A Stage 2: Common Crawl WARC fetch engine (gentle, resumeable).

Reads cc_index/*.jsonl, selects fetchable records (HTML + JSON API, skipping
robots/sitemaps/assets), dedupes by (url,timestamp), and fetches the exact
WARC byte-range from data.commoncrawl.org with a bounded worker pool.

Health-aware: 429/403 throttle triggers a backoff so we never hammer the
CloudFront edge into a block. Payloads are cached (gzipped) in cc_bodies/
keyed by content hash -> resumeable.

Priorities: api_web JSON > HTML item pages > HTML category pages.
Writes cc_bodies/queue.jsonl progress (resumeable).

Usage: python cc_fetch.py [max_records]
"""
import gzip, hashlib, json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.__file__))  # stdlib first (local inspect.py shadow)
import inspect  # cache stdlib inspect BEFORE requests/typing lazy-imports it
import concurrent.futures as cf
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
IDX_DIR = os.path.join(BASE, "cc_index")
BODY_DIR = os.path.join(BASE, "cc_bodies")
os.makedirs(BODY_DIR, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) research-archive-miner/2.0"}
WORKERS = int(os.environ.get("CC_WORKERS", "6"))
SESSION = requests.Session()

_lock = threading.Lock()
_pause_until = 0.0
_consec_403 = 0

def classify(rec):
    u = rec.get("url", "")
    mime = (rec.get("mime-detected") or rec.get("mime") or "").lower()
    path = u.split("?")[0].split("#")[0].lower()
    base = os.path.basename(path)
    if "api_web/v1/" in path:
        return "api" if ("/item" in path or "/listing" in path) else "api_other"
    if base in ("robots.txt",) or "sitemap" in base or base.endswith(".xml"):
        return "junk"
    if mime.startswith("text/html"):
        seg = os.path.splitext(base)[0]
        tail = seg.rsplit("-", 1)[-1] if "-" in seg else ""
        if len(tail) >= 18 and len(tail) <= 32:
            return "item_html"
        return "cat_html"
    return "junk"

def gate():
    with _lock:
        p = _pause_until
    if p and time.time() < p:
        time.sleep(p - time.time() + 0.2)

def note(status):
    global _pause_until, _consec_403
    with _lock:
        if status in (429, 403):
            _consec_403 += 1
            # exponential backoff: 30s, 60s, 120s, 240s...
            wait = min(30 * (2 ** (_consec_403 - 1)), 600)
            _pause_until = max(_pause_until, time.time() + wait)
        elif status in (200, 206):
            _consec_403 = 0

def fetch_record(rec):
    global _pause_until, _consec_403
    fn = rec["filename"]
    off = int(rec["offset"])
    ln = int(rec["length"])
    key = hashlib.sha1(f"{fn}:{off}:{ln}".encode()).hexdigest()
    out = os.path.join(BODY_DIR, key + ".gz")
    if os.path.exists(out):
        return key, True
    url = "https://data.commoncrawl.org/" + fn
    gate()
    try:
        r = SESSION.get(url, headers={"Range": f"bytes={off}-{off + ln - 1}"},
                        timeout=180, allow_redirects=False)
    except (requests.RequestException, OSError) as e:
        # transient conn error -> small backoff, count towards health
        with _lock:
            _consec_403 += 1
            wait = min(10 * (2 ** (_consec_403 - 1)), 300)
            _pause_until = max(_pause_until, time.time() + wait)
        return key, False
    if r.status_code not in (200, 206):
        note(r.status_code)
        return key, False
    note(r.status_code)
    data = r.content
    if fn.endswith(".gz") or data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError, ValueError):
            return key, False
    m = data.find(b"\r\n\r\n")
    if m >= 0:
        data = data[m + 4:]
    if not data:
        return key, False
    with open(out, "wb") as wf:
        wf.write(gzip.compress(data, 6))
    return key, True

def iter_records(max_recs):
    """Yield (cls, rec) without materializing the whole 718k queue in memory."""
    seen = set()
    n = 0
    for fn in sorted(os.listdir(IDX_DIR)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(IDX_DIR, fn), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                cls = classify(j)
                if cls == "junk":
                    continue
                dk = (j.get("url"), j.get("timestamp"))
                if dk in seen:
                    continue
                seen.add(dk)
                yield (cls, j)
                n += 1
                if max_recs and n >= max_recs:
                    return

def main():
    max_recs = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    log = os.path.join(BODY_DIR, "queue.jsonl")
    done_log = set()
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    if isinstance(rec, dict) and rec.get("key"):
                        done_log.add(rec["key"])

    ok = skip = fail = 0
    t0 = time.time()
    WINDOW = WORKERS * 2

    def handle(fut, futures):
        nonlocal ok, fail
        k2, was_ok = fut.result()
        cls2, rec2 = futures[fut]
        if was_ok:
            ok += 1
        else:
            fail += 1
        with open(log, "a", encoding="utf-8") as lg:
            lg.write(json.dumps({"key": k2, "cls": cls2,
                                 "url": rec2.get("url"), "ts": rec2.get("timestamp")}) + "\n")
        futures.pop(fut, None)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {}
        gen = iter_records(max_recs)
        for cls, rec in gen:
            k = hashlib.sha1(f"{rec['filename']}:{rec['offset']}:{rec['length']}".encode()).hexdigest()
            if k in done_log:
                skip += 1
                continue
            rec["key"] = k
            futures[ex.submit(fetch_record, rec)] = (cls, rec)
            # if the window is full block until at least one completes
            while len(futures) >= WINDOW:
                f = cf.wait(futures, return_when=cf.FIRST_COMPLETED)[0].pop()
                handle(f, futures)
            if (ok + fail) % 200 == 0 and (ok + fail) > 0:
                el = time.time() - t0
                print(f"  ok={ok} skip={skip} fail={fail} rate={(ok+fail)/max(el,1):.1f}/s {el:.0f}s",
                      flush=True)
        # drain
        while futures:
            f = cf.wait(futures, return_when=cf.FIRST_COMPLETED)[0].pop()
            handle(f, futures)
    print(f"done: ok={ok} skip={skip} fail={fail} fetched={ok+fail}", flush=True)

if __name__ == "__main__":
    main()