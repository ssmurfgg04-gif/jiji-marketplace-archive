"""Build bodyhash -> (url,ts,country) cache from cc_index/*.jsonl once."""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.__file__))
BASE = os.path.dirname(os.path.abspath(__file__))
IDX_DIR = os.path.join(BASE, "cc_index")
CACHE = os.path.join(BASE, "cc_meta.jsonl")

def main():
    n = 0
    with open(CACHE, "w", encoding="utf-8") as out:
        for fn in sorted(os.listdir(IDX_DIR)):
            if not fn.endswith(".jsonl"):
                continue
            name = fn[:-6]  # strip ".jsonl" -> "jiji.co.ke", "jiji.ng", ...
            COUNTRY = {"jiji.co.ke": "ke", "jiji.ng": "ng", "jiji.co.tz": "tz",
                       "jiji.co.ug": "ug", "jiji.co.za": "za", "jiji.et": "et",
                       "jiji.com.gh": "gh"}
            country = COUNTRY.get(name, name)
            with open(os.path.join(IDX_DIR, fn), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line)
                    except Exception:
                        continue
                    h = hashlib.sha1(f"{j['filename']}:{j['offset']}:{j['length']}".encode()).hexdigest()
                    ts = j.get("timestamp", "")
                    date = (ts[:4] + "-" + ts[4:6] + "-" + ts[6:8]) if len(ts) >= 8 else ""
                    out.write(json.dumps([h, j.get("url", ""), date, country]) + "\n")
                    n += 1
    print(f"cache: {n} keys")

if __name__ == "__main__":
    main()