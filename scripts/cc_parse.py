#!/usr/bin/env python3
"""PATH C Stage 1: Parse cc_bodies/*.gz WARC payloads into row records.

Each stored body is an HTTP response (headers + HTML or JSON). We detect:
  - API JSON  -> full item/listing rows
  - HTML item/cat -> card links: guid (from slug), title, price (KSh)

Output: cc_rows.tsv appended incrementally (resumeable by skipping done hashes).
Also emits cc_rows_lookup.tsv mapping body-hash -> (url, crawl_timestamp)
so temporal features can be inferred later.
"""
import gzip, hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.__file__))
import inspect
BASE = os.path.dirname(os.path.abspath(__file__))
BODY_DIR = os.path.join(BASE, "cc_bodies")
OUT = os.path.join(BASE, "cc_rows.tsv")
META = os.path.join(BASE, "cc_meta.jsonl")

GUID_SLUG = re.compile(r'([A-Za-z0-9_\-]{18,32})(?:\.html)?(?:"|\?|$)')
KSH = re.compile(r'KSh\s*([\d,]+(?:\.\d{0,2})?)')

def strip_tags(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def parse_html(payload, keys):
    """keys -> dict of static keys passed through (guid, url, ts, country...)."""
    rows = []
    anchors = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', payload, re.S)
    seen = set()
    for href, inner in anchors:
        if not re.search(r'jiji\.co\.ke', href):
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
            imgs = [im.get("url") for im in (adv.get("images") or []) if isinstance(im, dict) and im.get("url")]
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
                            "price": (ad.get("price_obj") or {}).get("value")},
                           )
                rows.append(row)
    return rows

FIELDNAMES = ["guid", "title", "price", "capture_ts", "source", "country", "bodyhash"]

def main():
    # load index metadata (url, crawl ts, country) by bodyhash from cache
    key_meta = {}
    with open(META, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                h, url, date, country = json.loads(line)
            except Exception:
                continue
            key_meta[h] = {"url": url, "ts": date, "country": country}
    print(f"metadata: {len(key_meta)} keys", flush=True)
    # only parse bodies flagged as fully-fetched (no partial writes)
    committed = set()
    qlog = os.path.join(BODY_DIR, "queue.jsonl")
    if os.path.exists(qlog):
        with open(qlog, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        committed.add(json.loads(line)["key"])
                    except Exception:
                        pass
    print(f"committed bodies: {len(committed)}", flush=True)

    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 7 and parts[6]:
                    done.add(parts[6])
    out = open(OUT, "a", encoding="utf-8")
    if os.path.getsize(OUT) == 0:
        out.write("#" + "\t".join(FIELDNAMES) + "\n")

    rows_written = 0
    for fn in sorted(os.listdir(BODY_DIR)):
        if not fn.endswith(".gz"):
            continue
        h = fn[:-3]
        if h in done or h not in committed:
            continue
        meta = key_meta.get(h, {"url": "", "ts": "", "country": ""})
        try:
            raw = gzip.decompress(open(os.path.join(BODY_DIR, fn), "rb").read())
        except Exception:
            continue
        m = raw.find(b"\r\n\r\n")
        head = raw[:m].decode("utf-8", "replace") if m >= 0 else ""
        payload = raw[m + 4:] if m >= 0 else raw
        if payload[:2] == b"\x1f\x8b":
            try:
                payload = gzip.decompress(payload)
            except Exception:
                pass
        text = payload.decode("utf-8", "replace")
        keys = {"capture_ts": meta["ts"], "country": meta["country"]}
        if re.search(r'"advert"\s*[:{]|"adverts_list"\s*[:{]', text):
            rows = json_rows(text, keys | {"source": "cc_api"})
        else:
            rows = parse_html(text, keys | {"source": "cc_html"})
        for r in rows:
            r["bodyhash"] = h
            out.write("\t".join(str(r.get(f, "")) for f in FIELDNAMES) + "\n")
            rows_written += 1
        done.add(h)
        if rows_written and rows_written % 5000 == 0:
            out.flush()
            print(f"  {rows_written} rows ({len(done)})", flush=True)
    out.close()
    print(f"done: wrote {rows_written} rows")

if __name__ == "__main__":
    main()