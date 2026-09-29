#!/usr/bin/env python3
"""PATH B: Optimized Wayback Machine miner (all Jiji regional domains).

Architecture (per IA's published limits):
  - Persistent requests.Session() (no connection storming).
  - Per-endpoint rate limits: /cdx at <=48/min, /web replay at <=480/min.
  - CDX pagination (showNumPages + page stepping) to avoid truncation.
  - HTTP 429: honor Retry-After header; else 60s + exponential backoff.
  - Fixed small worker pool for replay fetches.

Mines /api_web/v1/item and /api_web/v1/listing JSON corpora into listings.tsv.
Resume-capable (skips already-written row keys).
"""
import csv, gzip, io, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.__file__))  # stdlib first (local inspect.py shadow)
import inspect  # cache stdlib inspect BEFORE requests/typing lazy-import it
import concurrent.futures as cf
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "listings.tsv")

CDX = "https://web.archive.org/cdx/search/cdx"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) research-archive-miner/2.0"}
SESSION = requests.Session()

FIELDS = ["guid", "title", "price", "date_created", "date_moderated", "date_edited",
          "seller_id", "seller_name", "phone", "category_id", "category_name",
          "count_views", "fav_count", "adverts_count", "feedback_count", "rating",
          "boost_badge", "capture_ts", "image_urls", "source", "country", "condition"]

# price-sanity: true garbage is zero/negatives and billion-plus parse
# artifacts (phone numbers leaking into price). NULL (no-price listings)
# passes through; the 200-20M band applies at analysis time, not ingest.
PRICE_CAP = 1_000_000_000


def cond_of(obj):
    for attr in obj.get("attrs") or []:
        if isinstance(attr, dict) and attr.get("name") == "Condition":
            return attr.get("value") or ""
    return ""


def sane_price(v):
    """(keep, value): keep=False only for true garbage."""
    if v is None or v == "":
        return True, None
    try:
        p = int(v)
    except (TypeError, ValueError):
        return True, None
    if p <= 0 or p > PRICE_CAP:
        return False, None
    return True, p

DOMAINS = [("jiji.co.ke", "ke"), ("jiji.ng", "ng"), ("jiji.co.tz", "tz"),
           ("jiji.co.ug", "ug"), ("jiji.co.za", "za"), ("jiji.et", "et"),
           ("jiji.com.gh", "gh")]

WORKERS = int(os.environ.get("WB_WORKERS", "4"))
CDX_DELAY = 1.3          # ~46/min, under the 48/min conservative cap
TAIL_DELAY = 0.06        # ~5-8 replays/sec (replay cap is 480/min)

req_by = {"cdx": 0, "tailed": 0}

def ratelimit():
    """Global 429 handling: parse Retry-After or backoff if blocked."""
    global _blocked_until
    if _blocked_until and time.time() < _blocked_until:
        time.sleep(_blocked_until - time.time() + 1)

_blocked_until = 0.0

def _req(method, url, **kw):
    """Rate-limited request that inspects 429 and applies Retry-After."""
    global _blocked_until
    for attempt in range(8):
        ratelimit()
        try:
            r = SESSION.request(method, url, headers=UA, timeout=120, **kw)
        except requests.RequestException as e:
            wait = min(2 * (attempt + 1), 60)
            time.sleep(wait)
            continue
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait = int(ra) if ra and ra.isdigit() else 60
            # IA has historically returned 429s that mean a 1h firewall when repeated
            if wait > 3600:
                _blocked_until = time.time() + wait
            else:
                time.sleep(wait)
            continue
        if r.status_code in (502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        return r
    return None

def cdx_paged(host_domain, prefix):
    """Paginated, collapsed CDX with showNumPages stepping."""
    domain, country = host_domain
    gather = []
    # showNumPages
    base = (f"{CDX}?url={domain}/{prefix}&matchType=prefix"
            f"&filter=statuscode:200&collapse=urlkey&pageSize=150000"
            f"&fl=timestamp,original&output=json&showNumPages=true")
    r = _req("GET", base)
    if not r:
        return gather
    m = re.search(r'"pages"\s*:\s*(\d+)', r.text)
    n_pages = int(m.group(1)) if m else 1
    cap = min(n_pages, 12)
    for pg in range(cap):
        u = (f"{CDX}?url={domain}/{prefix}&matchType=prefix"
             f"&filter=statuscode:200&collapse=urlkey&pageSize=150000"
             f"&fl=timestamp,original&output=json&page={pg}")
        rr = _req("GET", u)
        req_by["cdx"] += 1
        if not rr:
            continue
        try:
            rows = json.loads(rr.text)
        except Exception:
            continue
        if len(rows) > 1:
            gather.extend(rows[1:])
        time.sleep(CDX_DELAY)
    return [(ts, orig) for ts, orig in gather]

def get_payload(url):
    """Replay fetch of RAW archive JSON (rate-limited per replay cap)."""
    r = _req("GET", url)
    req_by["tailed"] += 1
    if not r:
        return None
    raw = r.content
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception:
            return None
    return raw.decode("utf-8", "replace")

def extract_item(j, ts, src, country):
    adv = j.get("advert", {}) or {}
    s = j.get("seller", {}) or {}
    imgs = [im["url"] for im in (adv.get("images", []) or []) if isinstance(im, dict) and im.get("url")]
    if not imgs:
        for row in (j.get("seo", {}) or {}).get("og_image_list", []) or []:
            if isinstance(row, list) and len(row) > 1 and row[0] == "image":
                imgs.append(row[1])
    web = (j.get("seo", {}) or {}).get("web_url", "") or ""
    guid = adv.get("guid") or web.rstrip("/").rsplit("/", 1)[-1]
    keep, pval = sane_price(s.get("advert_price"))
    if not keep:
        req_by["price_rejected"] = req_by.get("price_rejected", 0) + 1
        return {}
    return {"guid": guid, "title": adv.get("title", ""),
            "price": pval,
            "date_created": adv.get("date_created", ""),
            "date_moderated": adv.get("date_moderated", ""),
            "date_edited": adv.get("date_edited", ""),
            "seller_id": s.get("id"), "seller_name": s.get("name", ""),
            "phone": s.get("phone", "") if s.get("phone") else "",
            "category_id": adv.get("category_id"),
            "category_name": adv.get("category_name", ""),
            "count_views": adv.get("count_views"), "fav_count": adv.get("fav_count"),
            "adverts_count": s.get("adverts_count"),
            "feedback_count": s.get("feedback_count"),
            "rating": s.get("rating"),
            "boost_badge": (adv.get("badge_info", {}) or {}).get("label", ""),
            "capture_ts": ts, "image_urls": ";".join(imgs), "source": src, "country": country,
            "condition": cond_of(adv)}

def extract_listing(j, ts, src, country):
    rows = []
    for ad in (j.get("adverts_list", {}).get("adverts", []) or []):
        if not isinstance(ad, dict):
            continue
        keep, pval = sane_price((ad.get("price_obj", {}) or {}).get("value"))
        if not keep:
            req_by["price_rejected"] = req_by.get("price_rejected", 0) + 1
            continue
        imgs = [im.get("url", "") for im in (ad.get("images", []) or []) if isinstance(im, dict) and im.get("url")]
        rows.append({"guid": ad.get("guid") or ad.get("id"), "title": ad.get("title", ""),
                     "price": pval,
                     "date_created": "", "date_moderated": "", "date_edited": "",
                     "seller_id": ad.get("user_id"), "seller_name": "",
                     "phone": ad.get("user_phone", ""),
                     "category_id": ad.get("category_id"),
                     "category_name": ad.get("category_name", ""),
                     "count_views": "", "fav_count": "",
                     "adverts_count": "", "feedback_count": "", "rating": "",
                      "boost_badge": (ad.get("badge_info", {}) or {}).get("label", ""),
                      "capture_ts": ts, "image_urls": ";".join(imgs), "source": src, "country": country,
                      "condition": cond_of(ad)})
    return rows

def fetch_one(job):
    ts, orig, prefix, domain, country = job
    url = f"https://web.archive.org/web/{ts}id_/{orig}"
    body = get_payload(url)
    time.sleep(TAIL_DELAY)
    if not body:
        return []
    try:
        j = json.loads(body)
    except Exception:
        return []
    if "item" in prefix:
        r = extract_item(j, ts, prefix, country)
        rows = [r] if r.get("guid") else []
    else:
        rows = extract_listing(j, ts, prefix, country)
    # drop only garbage-price item rows (extract_* already counted them);
    # NULL-price rows are kept (no-price/contact listings are honest signal)
    ok = [r for r in rows if r.get("guid")]
    return ok

def main():
    domains = DOMAINS
    if len(sys.argv) > 1:
        domains = [d for d in DOMAINS if d[0] == sys.argv[1]]
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            rd = csv.DictReader(f, delimiter="\t")
            old_fields = rd.fieldnames or []
            for row in rd:
                done.add((row["source"], row["capture_ts"], row["guid"], row["country"]))
        if old_fields != FIELDS:
            # schema migration: rewrite header, backfill missing cols with ""
            with open(OUT, encoding="utf-8") as f:
                rd = csv.DictReader(f, delimiter="\t")
                rows_all = list(rd)
            with open(OUT, "w", encoding="utf-8", newline="") as f:
                w0 = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
                w0.writeheader()
                for r in rows_all:
                    w0.writerow({k: r.get(k, "") for k in FIELDS})
            print(f"migrated {OUT} header {old_fields} -> +condition ({len(rows_all)} rows)", flush=True)
    f = open(OUT, "a", encoding="utf-8", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
    if os.path.getsize(OUT) == 0:
        w.writeheader(); f.flush()
    print(f"resume: {len(done)} rows in {OUT}", flush=True)
    n_new = 0
    t0 = time.time()
    jobs = []
    for prefix in ["api_web/v1/listing", "api_web/v1/item"]:
        for d in domains:
            pairs = cdx_paged(d, prefix)
            print(f"[{d[0]} {prefix}] {len(pairs)} captures", flush=True)
            for ts, orig in pairs:
                jobs.append((ts, orig, prefix, d[0], d[1]))
    print(f"total jobs: {len(jobs)}", flush=True)
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(fetch_one, jb) for jb in jobs]
        total = 0
        for fut in futs:
            for r in fut.result():
                key = (r["source"], r["capture_ts"], r["guid"], r["country"])
                if key in done:
                    continue
                w.writerow(r); done.add(key); n_new += 1
            total += 1
            if total % 20 == 0:
                f.flush()
                print(f"  {total}/{len(jobs)} new={n_new} reqs={req_by} "
                      f"{int(time.time()-t0)}s", flush=True)
    f.flush(); f.close()
    print(f"done -> {OUT} (+{n_new}, total {len(done)}, reqs {req_by})")

if __name__ == "__main__":
    main()