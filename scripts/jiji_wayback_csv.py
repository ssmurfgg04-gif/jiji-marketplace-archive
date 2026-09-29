#!/usr/bin/env python3
"""Mine Wayback /api_web/v1/{item,listing} corpora into listings.csv (multi-domain).
Passive archival mining only. Resume-capable, polite, small thread pool."""
import csv, gzip, io, json, os, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

CDX = "https://web.archive.org/cdx/search/cdx"
RAW = "https://web.archive.org/web/{ts}id_/{url}"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) research-archive-miner/1.0"}
FIELDS = ["guid","title","price","date_created","date_moderated","date_edited",
          "seller_id","seller_name","phone","category_id","category_name",
          "count_views","fav_count","adverts_count","feedback_count","rating",
          "boost_badge","capture_ts","image_urls","source","country"]
DOMAINS = [("jiji.co.ke","ke"), ("jiji.ng","ng"), ("jiji.co.tz","tz"), ("jiji.co.ug","ug"), ("jiji.co.za","za")]
WORKERS = int(os.environ.get("MINER_WORKERS", "2"))
SLEEP = float(os.environ.get("MINER_SLEEP", "0.5"))
MAX_REQS = int(os.environ.get("MINER_MAX_REQS", "9000"))

_req_count = 0

def get(url, tries=3):
    global _req_count
    for i in range(tries):
        try:
            time.sleep(SLEEP)
            req = urllib.request.Request(url, headers=UA)
            _req_count += 1
            raw = urllib.request.urlopen(req, timeout=60).read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return raw.decode("utf-8", "replace")
        except Exception:
            if i == tries - 1: return None
            time.sleep(2 + i * 3)

def cdx_list(host, prefix):
    q = (f"{CDX}?url={host}/{prefix}&matchType=prefix&filter=statuscode:200"
         f"&fl=timestamp,original&output=json&limit=100000")
    data = get(q)
    if not data: return []
    try: rows = json.loads(data)
    except Exception: return []
    return [(ts, orig) for ts, orig in rows[1:]]

def extract_item(j, ts, src, country):
    adv = j.get("advert", {}) or {}
    s = j.get("seller", {}) or {}
    imgs = [im["url"] for im in (adv.get("images", []) or []) if im.get("url")]
    if not imgs:
        for row in (j.get("seo", {}) or {}).get("og_image_list", []) or []:
            if isinstance(row, list) and len(row) > 1 and row[0] == "image":
                imgs.append(row[1])
    web = (j.get("seo", {}) or {}).get("web_url", "") or ""
    guid = adv.get("guid") or web.rstrip("/").rsplit("/", 1)[-1]
    return {"guid": guid, "title": adv.get("title", ""),
            "price": s.get("advert_price"),
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
            "capture_ts": ts, "image_urls": ";".join(imgs), "source": src, "country": country}

def extract_listing(j, ts, src, country):
    rows = []
    for ad in (j.get("adverts_list", {}).get("adverts", []) or []):
        imgs = [im.get("url", "") for im in (ad.get("images", []) or []) if im.get("url")]
        rows.append({"guid": ad.get("guid") or ad.get("id"), "title": ad.get("title", ""),
                     "price": (ad.get("price_obj", {}) or {}).get("value"),
                     "date_created": "", "date_moderated": "", "date_edited": "",
                     "seller_id": ad.get("user_id"), "seller_name": "",
                     "phone": ad.get("user_phone", ""),
                     "category_id": ad.get("category_id"),
                     "category_name": ad.get("category_name", ""),
                     "count_views": "", "fav_count": "",
                     "adverts_count": "", "feedback_count": "", "rating": "",
                     "boost_badge": (ad.get("badge_info", {}) or {}).get("label", ""),
                     "capture_ts": ts, "image_urls": ";".join(imgs), "source": src, "country": country})
    return rows

def fetch_one(job):
    ts, orig, prefix, domain, country = job
    body = get(RAW.format(ts=ts, url=urllib.parse.quote(orig, safe=":/?&=%")))
    if not body: return []
    try: j = json.loads(body)
    except Exception: return []
    fn = extract_item if "item" in prefix else extract_listing
    rows = fn(j, ts, prefix, country)
    if not isinstance(rows, list): rows = [rows]
    return [r for r in rows if r["guid"]]

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "listings.csv"
    domains = DOMAINS
    if len(sys.argv) > 2:
        domains = [(d, c) for d, c in DOMAINS if d == sys.argv[2]]
    done = set()
    if os.path.exists(out):
        with open(out, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                done.add((row["source"], row["capture_ts"], row["guid"], row["country"]))
    f = open(out, "a", encoding="utf-8", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if not done: w.writeheader(); f.flush()
    print(f"resume: {len(done)} rows already in {out}", flush=True)
    n_new = 0
    t0 = time.time()
    for prefix in ["api_web/v1/listing", "api_web/v1/item"]:
        pairs = []
        jd = []
        for domain, country in domains:
            for ts, orig in cdx_list(domain, prefix):
                pairs.append((ts, orig)); jd.append((domain, country))
        print(f"[{prefix}] {len(pairs)} captures total", flush=True)
        if not pairs or _req_count >= MAX_REQS: continue
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            k = total = 0
            while k < len(pairs) and _req_count < MAX_REQS:
                chunk_end = min(k + 300, len(pairs))
                jobs = [(pairs[i][0], pairs[i][1], prefix, jd[i][0], jd[i][1])
                        for i in range(k, chunk_end)]
                futs = [ex.submit(fetch_one, jb) for jb in jobs]
                for fut in futs:
                    for r in fut.result():
                        key = (r["source"], r["capture_ts"], r["guid"], r["country"])
                        if key in done: continue
                        w.writerow(r); done.add(key); n_new += 1
                    total += 1
                    if total % 20 == 0:
                        f.flush()
                        print(f"  {total}/{len(pairs)} reqs={_req_count} new={n_new} "
                              f"lines={len(done)} {int(time.time()-t0)}s", flush=True)
                k = chunk_end
            if _req_count >= MAX_REQS:
                print("req cap hit", flush=True)
        f.flush()
    f.close()
    print(f"done -> {out} (+{n_new} new rows, total rows {len(done)}, reqs {_req_count})")

if __name__ == "__main__":
    main()