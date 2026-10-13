"""Merge worker rows_NN.tsv.gz artifacts into cc_rows.tsv, dedup by bodyhash."""
import gzip, os, sys

BASE = "C:\\Users\\Jackb\\Downloads\\New folder (2)"
ART_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "cc_worker_rows")
OUT = os.path.join(BASE, "cc_rows.tsv")
FIELDNAMES = ["guid", "title", "price", "capture_ts", "source", "country", "bodyhash", "url", "category_name"]
N_COLS = len(FIELDNAMES)

def split_row(line):
    parts = line.split("\t")
    if len(parts) < 7:
        return None
    # pad pre-url-era 7-col rows so old + new files merge cleanly
    if len(parts) < N_COLS:
        parts = parts + [""] * (N_COLS - len(parts))
    return parts[:N_COLS]

def main():
    seen = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = split_row(line.rstrip("\n"))
                if parts and parts[6]:
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
                    parts = split_row(line)
                    if not parts:
                        continue
                    h = parts[6]
                    if h in seen:
                        continue
                    seen.add(h)
                    out.write("\t".join(parts) + "\n")
                    added += 1
            print(f"  {fn}: cumulative added {added}", flush=True)
    print(f"done: before={before} added={added} total={len(seen)}")

if __name__ == "__main__":
    main()