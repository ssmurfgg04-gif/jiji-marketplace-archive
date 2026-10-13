"""Image pHash layer for Jiji fraud detection (stock/reused-photo bait).

Why this exists: the pipeline dedups by byte hashes (WARC bodyhash, BLAKE3
records). Those never fire when the SAME photo is re-uploaded across listings
(T490 title + T510 photos). A 64-bit dHash (pure PIL, no new deps) catches it.

Usage:
  python scripts/phash_images.py --guid <guid> [--guid <guid> ...]
  python scripts/phash_images.py --slug computers-and-laptops --query "thinkpad" --max-pages 2 --sort price

Storage: data/image_hashes.db (separate from jiji.db on purpose — the live DB
is zipped by the home-runner release; this must never lock it).
Tables: images(guid, url, dhash, fetched_at), reuse computed by query.
Resume-safe: skips (guid, url) pairs already stored.

Polite: single worker, 1-3s random delays, Jiji CDN only.
"""
import argparse
import json
import os
import random
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "..", "data")
DB = os.path.join(DATA, "image_hashes.db")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jiji-phash/1.0"}
LISTING = "https://jiji.co.ke/api_web/v1/listing"
ITEM = "https://jiji.co.ke/api_web/v1/item/"


def dhash(img):
    """64-bit difference hash as 16-char hex. Pure PIL."""
    img = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
    return "%016x" % bits


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def fetch_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def fetch_image(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                import io
                return Image.open(io.BytesIO(r.read()))
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def item_images(guid):
    d = fetch_json(ITEM + guid)
    if not d:
        return [], ""
    adv = d.get("advert", {}) or {}
    imgs = [im.get("url", "") for im in (adv.get("images", []) or []) if im.get("url")]
    return imgs, adv.get("title", "")


def listing_adverts(query, slug, sort, page):
    q = {"slug": slug, "init_page": "true", "webp": "true",
         "sort": sort, "page": str(page)}
    if query:
        q["query"] = query
    d = fetch_json(LISTING + "?" + urllib.parse.urlencode(q))
    if not d:
        return []
    return ((d.get("adverts_list") or {}).get("adverts")) or []


SCHEMA = """
CREATE TABLE IF NOT EXISTS images(
  guid TEXT, url TEXT, dhash TEXT, title TEXT, fetched_at TEXT,
  PRIMARY KEY (guid, url));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guid", action="append", default=[])
    ap.add_argument("--query", default="")
    ap.add_argument("--slug", default="computers-and-laptops")
    ap.add_argument("--sort", default="price")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--delay-min", type=float, default=1.0)
    ap.add_argument("--delay-max", type=float, default=3.0)
    ap.add_argument("--near", type=int, default=5,
                    help="hamming distance for near-dupe report")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    db = sqlite3.connect(os.path.join(BASE, "..", "data", "image_hashes.db"))
    db.execute(SCHEMA)

    targets = []  # (guid, title_hint)
    if args.guid:
        for g in args.guid:
            targets.append((g, ""))
    else:
        for page in range(1, args.max_pages + 1):
            for ad in listing_adverts(args.query, args.slug, args.sort, page):
                g = ad.get("guid") or str(ad.get("id", ""))
                if g:
                    targets.append((g, ad.get("title", "")))
            time.sleep(random.uniform(args.delay_min, args.delay_max))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = 0
    for guid, hint in targets:
        have = {r[0] for r in db.execute(
            "SELECT url FROM images WHERE guid=?", (guid,))}
        imgs, title = item_images(guid)
        title = title or hint
        for u in imgs:
            if u in have:
                continue
            time.sleep(random.uniform(args.delay_min, args.delay_max))
            img = fetch_image(u)
            if img is None:
                continue
            try:
                h = dhash(img)
            except Exception:
                continue
            db.execute(
                "INSERT OR IGNORE INTO images VALUES(?,?,?,?,?)",
                (guid, u, h, title, now))
            db.commit()
            new += 1
            print(f"  {guid[:8]} {h} {title[:50]}", flush=True)

    rows = db.execute("SELECT guid, url, dhash, title FROM images").fetchall()
    print(f"images stored: {len(rows)} (+{new} new)")

    # exact-reuse report: same dhash, 2+ distinct guids = shared/stock photo
    by_hash = {}
    for guid, url, h, title in rows:
        by_hash.setdefault(h, []).append((guid, title))
    reused = {h: v for h, v in by_hash.items()
              if len({g for g, _ in v}) > 1}
    print(f"reused-photo groups (exact dhash, 2+ listings): {len(reused)}")
    for h, v in sorted(reused.items(), key=lambda kv: -len(kv[1]))[:20]:
        print(f"  dhash={h} listings={len(v)}")
        for g, t in v[:6]:
            print(f"    {g} | {(t or '')[:70]}")

    # near-dupe pairs within --near bits (O(n^2); fine to ~10k rows, BK-tree later)
    if len(rows) < 20000:
        seen_pairs = 0
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if rows[i][0] == rows[j][0]:
                    continue
                if hamming(rows[i][2], rows[j][2]) <= args.near:
                    if seen_pairs < 20:
                        print(f"  near({hamming(rows[i][2], rows[j][2])}): "
                              f"{rows[i][0][:8]} {(rows[i][3] or '')[:40]} <-> "
                              f"{rows[j][0][:8]} {(rows[j][3] or '')[:40]}")
                    seen_pairs += 1
        print(f"near-dupe pairs (<={args.near} bits, cross-listing): {seen_pairs}")
    db.close()


if __name__ == "__main__":
    main()
