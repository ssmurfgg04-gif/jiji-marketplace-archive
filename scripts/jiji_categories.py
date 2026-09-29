"""Crawl the full Jiji category tree via devalue index resolution.

Each category page embeds its children as {id,parent_id,slug,name} quads with
string-table indirection. Fetch top-level slugs, then each child page for
grandchildren (leaf categories like 284-processors). Polite, resumeable.

Usage: python scripts/jiji_categories.py [--seed data/category_slugs.txt]
Output: data/category_tree.tsv  (slug, id, parent_id, name, depth)
"""
import argparse
import json
import os
import random
import re
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "..", "data")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jiji-snapshot/1.0"}


def fetch(url, tries=3):
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (a + 1))
    print(f"  !! fetch failed {url}: {last}")
    return ""


def children_of(slug):
    html = fetch(f"https://jiji.co.ke/{slug}")
    if not html:
        return []
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return []

    def resolve(v):
        return arr[v] if isinstance(v, int) and 0 <= v < len(arr) else v

    found = []

    def walk(o):
        if isinstance(o, dict):
            if {"id", "parent_id", "slug", "name"} <= set(o):
                try:
                    found.append((resolve(o["id"]), resolve(o["parent_id"]),
                                  resolve(o["slug"]), resolve(o["name"])))
                except Exception:
                    pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(arr)
    out = []
    for cid, pid, s, name in found:
        if (isinstance(s, str) and s and isinstance(name, str) and "/" not in s
                and isinstance(cid, (int, str)) and not isinstance(cid, bool)):
            out.append((s, cid, pid, name))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=os.path.join(DATA, "category_slugs.txt"))
    ap.add_argument("--out", default=os.path.join(DATA, "category_tree.tsv"))
    ap.add_argument("--dmin", type=float, default=1.0)
    ap.add_argument("--dmax", type=float, default=2.5)
    args = ap.parse_args()

    with open(args.seed, encoding="utf-8") as f:
        seeds = [l.strip() for l in f if l.strip()]
    nodes = {}  # slug -> (id, parent, name, depth)
    queue = [(s, 0) for s in seeds]
    seen_pages = set()
    visited_file = args.out + ".visited"

    def flush():
        with open(args.out, "w", encoding="utf-8") as f:
            for s in sorted((k for k in nodes if isinstance(k, str) and k), key=str):
                cid, pid, name, depth = nodes[s]
                f.write(f"{s}\t{cid}\t{pid}\t{name}\t{depth}\n")
        with open(visited_file, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(seen_pages)))

    if os.path.exists(args.out):  # resume without refetching visited pages
        with open(args.out, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 5:
                    s, cid, pid, name, depth = parts
                    nodes[s] = (cid, pid, name, int(depth))
    if os.path.exists(visited_file):
        with open(visited_file, encoding="utf-8") as f:
            seen_pages = {l.strip() for l in f if l.strip()}
        for s in nodes:
            if s not in seen_pages:
                queue.append((s, nodes[s][3]))
        print(f"resumed: {len(nodes)} nodes, {len(seen_pages)} pages visited")
    while queue:
        slug, depth = queue.pop(0)
        if slug in seen_pages:
            continue
        seen_pages.add(slug)
        if depth > 1:
            # Depth 2+ pages only re-embed the same global tree already
            # captured from depth 0/1 pages; fetching them adds nothing.
            flush()
            continue
        time.sleep(random.uniform(args.dmin, args.dmax))
        kids = children_of(slug)
        print(f"{slug}: {len(kids)} child nodes (depth {depth})", flush=True)
        for s, cid, pid, name in kids:
            if s not in nodes:
                nodes[s] = (cid, pid, name, depth + 1)
            if s not in seen_pages:
                queue.append((s, depth + 1))
        flush()
    print(f"wrote {args.out} nodes={len(nodes)} pages={len(seen_pages)}")


if __name__ == "__main__":
    main()
