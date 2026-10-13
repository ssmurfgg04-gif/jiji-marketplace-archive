#!/usr/bin/env python3
"""CC matrix worker: fetch a chunk of WARC records and parse rows inline.

Reads cc_manifest/chunk_NN.tsv (filename\toffset\tlength\turl\tts\tmime\tkey),
fetches each byte-range from data.commoncrawl.org (async, rate-capped to stay
under the per-IP CloudFront throttle), parses the body with the same extraction
logic as cc_parse.py, and writes rows_NN.tsv.gz (same FIELDNAMES schema).

Usage: python cc_fetch_worker.py --chunk NN [--out DIR]
Output: rows_NN.tsv.gz next to the manifest (or --out dir).
"""
import asyncio, gzip, hashlib, json, os, re, sys, time

import aiohttp

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_DIR = os.path.join(HERE, "cc_manifest")
UA = "Mozilla/5.0 (X11; Linux x86_64) research-archive-miner/2.2"
CONCURRENCY = int(os.environ.get("CC_ASYNC_CONCURRENCY", "25"))
RPS_CAP = float(os.environ.get("CC_RPS_CAP", "12"))

HOST_COUNTRY = {"jiji.co.ke": "ke", "jiji.ng": "ng", "jiji.co.tz": "tz",
                "jiji.co.ug": "ug", "jiji.co.za": "za", "jiji.et": "et",
                "jiji.com.gh": "gh"}

def country_of(url):
    host = url.split("//")[1].split("/")[0] if "//" in url else url.split("/")[0]
    host = host.split(":")[0].lower()
    import re as _re
    m = _re.search(r"(jiji\.[a-z0-9.]+)", host)
    key = m.group(1) if m else host
    return HOST_COUNTRY.get(key, key)

GUID_SLUG = re.compile(r'([A-Za-z0-9_\-]{18,32})(?:\.html)?(?:"|\?|$)')
KSH = re.compile(r'(?:KSh|KShs|Ksh|Sh)\s*([\d,]+(?:\.\d{0,2})?)')


def strip_tags(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def parse_html(payload, keys):
    rows = []
    anchors = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', payload, re.S)
    seen = set()
    for href, inner in anchors:
        if not re.search(r'jiji\.(co\.)?', href):
            continue
        seg = href.split('?')[0].rstrip('/')
        tail = seg.rsplit('/', 1)[-1]
        m = GUID_SLUG.search(tail)
        if not m:
            continue
        guid = m.group(1)
        if len(guid) < 18 or guid in seen:
            continue
        seen.add(guid)
        pm = KSH.search(strip_tags(inner)) or KSH.search(payload)
        price = pm.group(1) if pm else None
        txt = strip_tags(inner)
        row = dict(keys)
        row.update({"guid": guid, "title": txt[:220], "price": price})
        rows.append(row)
    return rows


def json_rows(payload, keys):
    try:
        j = json.loads(payload)
    except Exception:
        return []
    rows = []
    if isinstance(j, dict):
        adv = j.get("advert") or {}
        seller = j.get("seller") or {}
        if adv and adv.get("guid"):
            row = dict(keys)
            row.update({"guid": adv.get("guid"), "title": adv.get("title", ""),
                        "price": (seller or {}).get("advert_price"),
                        "category_id": adv.get("category_id"),
                        "category_name": adv.get("category_name", ""),
                        "date_created": adv.get("date_created", ""),
                        "seller_id": (seller or {}).get("id"),
                        "count_views": adv.get("count_views")})
            rows.append(row)
            return rows
        for ad in ((j.get("adverts_list") or {}).get("adverts") or []):
            if isinstance(ad, dict) and ad.get("guid"):
                row = dict(keys)
                row.update({"guid": ad.get("guid"), "title": ad.get("title", ""),
                            "price": (ad.get("price_obj") or {}).get("value"),
                            "category_name": ad.get("category_name", "")})
                rows.append(row)
    return rows


def parse_body(raw, keys):
    m = raw.find(b"\r\n\r\n")
    payload = raw[m + 4:] if m >= 0 else raw
    if payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    text = payload.decode("utf-8", "replace")
    if re.search(r'"advert"\s*[:{]|"adverts_list"\s*[:{]', text):
        return json_rows(text, keys | {"source": "cc_api"})
    return parse_html(text, keys | {"source": "cc_html"})


class Bucket:
    def __init__(self, rate):
        self.rate = rate
        self.tokens = rate
        self.updated = time.monotonic()
        self.mtx = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self.mtx:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
            await asyncio.sleep(0.05)


async def fetch_one(session, rec, bucket, pause, lock):
    fn, off_s, ln_s, url, ts, mime, key = rec
    off, ln = int(off_s), int(ln_s)
    out_url = "https://data.commoncrawl.org/" + fn
    headers = {"Range": f"bytes={off}-{off + ln - 1}"}
    if bucket:
        await bucket.acquire()
    try:
        async with session.get(out_url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status not in (200, 206):
                if r.status in (429, 403, 503):
                    async with lock:
                        pause[0] = max(time.time() + 20, pause[0])
                return None
            data = await r.read()
    except (aiohttp.ClientError, OSError):
        return None
    if not data:
        return None
    return data


async def main():
    args = sys.argv[1:]
    chunk = None
    out_dir = HERE
    manifest_path = os.path.join(MANIFEST_DIR, f"chunk_{int(chunk):02d}.tsv") if chunk else None
    i = 0
    while i < len(args):
        if args[i] == "--chunk":
            chunk = args[i + 1]; i += 2
        elif args[i] == "--out":
            out_dir = args[i + 1]; i += 2
        elif args[i] == "--manifest":
            manifest_path = args[i + 1]; i += 2
        else:
            i += 1
    if chunk is None:
        print("usage: cc_fetch_worker.py --chunk NN [--manifest PATH] [--out DIR]", flush=True)
        sys.exit(2)
    recs = []
    if manifest_path is None:
        manifest_path = os.path.join(MANIFEST_DIR, f"chunk_{int(chunk):02d}.tsv")
    with open(manifest_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            recs.append(line.split("\t"))
    t0 = time.time()
    print(f"chunk {chunk}: {len(recs)} records", flush=True)

    bucket = Bucket(RPS_CAP) if RPS_CAP else None
    pause = [0.0]
    lock = asyncio.Lock()
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, limit_per_host=CONCURRENCY,
                                     enable_cleanup_closed=True, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector,
                                     timeout=aiohttp.ClientTimeout(total=180, connect=30),
                                     headers={"User-Agent": UA}) as session:
        done = ok = fail = 0
        rows_all = []
        batch = []
        idx = 0
        while idx < len(recs):
            while pause[0] and time.time() < pause[0]:
                await asyncio.sleep(0.5)
            end = min(idx + CONCURRENCY * 2, len(recs))
            batch = recs[idx:end]
            tasks = [fetch_one(session, r, bucket, pause, lock) for r in batch]
            results = await asyncio.gather(*tasks)
            for rec, data in zip(batch, results):
                done += 1
                if data is None:
                    fail += 1
                    continue
                ok += 1
                fn, off_s, ln_s, url, ts, mime, key = rec
                date = (ts[:4] + "-" + ts[4:6] + "-" + ts[6:8]) if len(ts) >= 8 else ""
                rows = parse_body(data, {"capture_ts": date, "country": country_of(url), "url": url})
                for r in rows:
                    r["bodyhash"] = key
                    rows_all.append(r)
                if done % 200 == 0:
                    el = time.time() - t0
                    print(f"  ok={ok} fail={fail} rate={done/max(el,1):.1f}/s {el:.0f}s rows={len(rows_all)}",
                          flush=True)
            idx = end
        print(f"done: ok={ok} fail={fail} rows={len(rows_all)} {time.time()-t0:.0f}s", flush=True)
        out = os.path.join(out_dir, f"rows_{int(chunk):02d}.tsv.gz")
        FIELDNAMES = ["guid", "title", "price", "capture_ts", "source", "country", "bodyhash", "url", "category_name"]
        with gzip.open(out, "wt", encoding="utf-8", newline="") as wf:
            wf.write("#" + "\t".join(FIELDNAMES) + "\n")
            for r in rows_all:
                wf.write("\t".join(str(r.get(f, "")) for f in FIELDNAMES) + "\n")
        print(f"wrote {out} ({os.path.getsize(out)} bytes)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())