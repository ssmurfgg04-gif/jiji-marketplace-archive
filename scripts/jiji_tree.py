"""Authoritative Jiji category tree via api_web/v1/categories (clean JSON).

Replaces HTML scraping: one call, complete tree with ids/parents/names.
Output: data/category_tree.tsv (slug, id, parent_id, name, depth).
"""
import json
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jiji-snapshot/1.0"}
OUT = "C:/Users/Jackb/Downloads/New folder (2)/data/category_tree.tsv"


def fetch_tree():
    req = urllib.request.Request("https://jiji.co.ke/api_web/v1/categories", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["data"]["categories"]


def main():
    cats = fetch_tree()
    print("nodes:", len(cats))
    by_id = {c["id"]: c for c in cats}

    def depth(c, seen=None):
        seen = seen or set()
        d, cur = 0, c
        while cur.get("parent_id") in by_id and cur["parent_id"] not in seen:
            seen.add(cur["parent_id"])
            cur = by_id[cur["parent_id"]]
            d += 1
            if d > 10:
                break
        return d

    rows = []

    def walk(nodes, depth):
        for c in nodes:
            slug = (c.get("slug") or "").strip()
            name = (c.get("name") or "").strip()
            if slug and "/" not in slug:
                rows.append((slug, c.get("id"), c.get("parent_id"), name, depth))
            for k in c.get("childes") or []:
                walk([k], depth + 1)

    walk(cats, 0)
    rows.sort()
    rows.sort()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"# fetched {stamp} nodes={len(rows)}\n")
        for slug, cid, pid, name, d in rows:
            f.write(f"{slug}\t{cid}\t{pid}\t{name}\t{d}\n")
    print(f"wrote {OUT} rows={len(rows)} maxdepth={max(r[4] for r in rows)}")
    # leaf/type slugs of interest for deal-hunting spot checks
    for want in ("284-processors", "16-desktop-computers", "computer-hardware"):
        hit = [r for r in rows if r[0] == want]
        print(want, "->", hit if hit else "NOT in tree (type-filter view, not a category)")


if __name__ == "__main__":
    main()
