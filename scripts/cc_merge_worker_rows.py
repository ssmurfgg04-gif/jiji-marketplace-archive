"""Merge worker rows_NN.tsv.gz artifacts into cc_rows.tsv, dedup by bodyhash."""
import gzip, os, sys

BASE = "C:\\Users\\Jackb\\Downloads\\New folder (2)"
ART_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "cc_worker_rows")
OUT = os.path.join(BASE, "cc_rows.tsv")
FIELDNAMES = ["guid", "title", "price", "capture_ts", "source", "country", "bodyhash"]

def main():
    seen = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 7 and parts[6]:
                    seen.add(parts[6])
    before = len(seen)
    added = 0
    with open(OUT, "a", encoding="utf-8") as out:
        if os.path.getsize(OUT) == 0:
            out.write("#" + "\t".join(FIELDNAMES) + "\n")
        files = sorted(f for f in os.listdir(ART_DIR) if f.endswith(".tsv.gz"))
        for fn in files:
            with gzip.open(os.path.join(ART_DIR, fn), "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) < 7:
                        continue
                    h = parts[6]
                    if h in seen:
                        continue
                    seen.add(h)
                    out.write(line + "\n")
                    added += 1
            print(f"  {fn}: cumulative added {added}", flush=True)
    print(f"done: before={before} added={added} total={len(seen)}")

if __name__ == "__main__":
    main()