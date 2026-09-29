"""Partition cc_index/*.jsonl records into 20 worker chunks (by key hash % 20).
Mirrors cc_fetch_async.iter_records: junk filtered, deduped by (url, timestamp).
Each chunk tsv: filename\toffset\tlength\turl\ttimestamp\tmime\tkey
"""
import hashlib, json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
IDX_DIR = os.path.join(BASE, "cc_index")
OUT_DIR = os.path.join(BASE, "cc_manifest")
os.makedirs(OUT_DIR, exist_ok=True)
N_WORKERS = 20


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


def key_of(rec):
    return hashlib.sha1(
        f"{rec['filename']}:{rec['offset']}:{rec['length']}".encode()
    ).hexdigest()


def main():
    handles = [open(os.path.join(OUT_DIR, f"chunk_{i:02d}.tsv"), "w", encoding="utf-8")
               for i in range(N_WORKERS)]
    seen = set()
    n = junk = dup = 0
    for fn in sorted(os.listdir(IDX_DIR)):
        if not fn.endswith(".jsonl"):
            continue
        print("reading", fn, flush=True)
        with open(os.path.join(IDX_DIR, fn), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if classify(j) == "junk":
                    junk += 1
                    continue
                dk = (j.get("url"), j.get("timestamp"))
                if dk in seen:
                    dup += 1
                    continue
                seen.add(dk)
                k = key_of(j)
                w = int(k[:8], 16) % N_WORKERS
                hand = handles[w]
                hand.write("\t".join([
                    j.get("filename", ""), str(j.get("offset", "")), str(j.get("length", "")),
                    j.get("url", ""), j.get("timestamp", ""),
                    (j.get("mime-detected") or j.get("mime") or ""), k
                ]) + "\n")
                n += 1
                if n % 100000 == 0:
                    print(f"  {n} records...", flush=True)
    for h in handles:
        h.close()
    print(f"total={n} junk={junk} dup={dup}")
    for i in range(N_WORKERS):
        p = os.path.join(OUT_DIR, f"chunk_{i:02d}.tsv")
        print(f"chunk_{i:02d}: {os.path.getsize(p)} bytes")


if __name__ == "__main__":
    main()