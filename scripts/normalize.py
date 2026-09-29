"""Normalized product identity: brand + model family canonicalization."""
import io
import re
import sqlite3
import sys
import time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BRANDS = ["lenovo", "hp", "dell", "apple", "asus", "acer", "toshiba", "samsung",
          "microsoft", "fujitsu", "msi", "huawei", "lg", "sony", "panasonic",
          "tecno", "infinix", "xiaomi", "redmi", "oppo", "vivo", "itel", "nokia",
          "toyota", "nissan", "honda", "mazda", "subaru", "mitsubishi", "mercedes",
          "bmw", "audi", "volkswagen", "yamaha", "bajaj", "tvsmotor", "sony"]
FAMILIES = ["thinkpad", "thinkcentre", "ideapad", "yoga", "legion",
            "elitebook", "probook", "pavilion", "envy", "omen", "compaq",
            "latitude", "inspiron", "vostro", "xps", "optiplex", "precision", "alienware",
            "macbook", "imac", "surface", "probook", "dynabook", "satellite",
            "vivobook", "zenbook", "aspire", "swift", "lifebook", "galaxy",
            "iphone", "playstation", "xbox", "pixel", "spark", "camon", "hot",
            "poco", "honor", "magic", "realme", "narzo", "blade", "pop",
            "mirage", "probox", "fielder", "vitz", "premio", "allion", "wish",
            "note", "demio", "fielder", "rav4", "harrier", "forester", "impreza",
            "legacy", "outback", "xv", "fit", "vezel", "cr-v", "civic", "accord",
            "corolla", "camry", "hilux", "land cruiser", "prado", "rav 4",
            "note", "march", "tiida", "sylphy", "x-trail", "navara", "caravan",
            "every", "wagon", "alto", "swift", "vitara", "jimny", "cervo"]


BRAND_RES = [(b, re.compile(r"\b" + re.escape(b) + r"\b")) for b in BRANDS]
GEN_MODEL = re.compile(r"\b([a-z]{2,}\s?\d{2,}[a-z0-9]*)\b")
# short/substring-risky families only count when followed by a model number
DIGIT_FAMS = {"hot", "pop", "go", "note", "fit", "march", "blade", "wish",
              "alto", "magic"}
FAM_RES = []
for _f in FAMILIES:
    if _f in DIGIT_FAMS:
        FAM_RES.append((_f, re.compile(r"\b" + re.escape(_f) + r"\s*\d")))
    else:
        FAM_RES.append((_f, re.compile(re.escape(_f))))


def normalize(title):
    t = (title or "").lower()
    brand = next((b for b, rx in BRAND_RES if rx.search(t)), "")
    fam = next((f for f, rx in FAM_RES if rx.search(t)), "")
    model = ""
    if fam:
        m = re.search(re.escape(fam) + r"[\s\-]*([a-z0-9]+(?:[\s\-][a-z0-9]+){0,2})", t)
        if m:
            model = m.group(1).strip(" -")
    else:
        m = GEN_MODEL.search(t)
        if m:
            model = m.group(1)
    parts = [p for p in [brand.title(), fam.title() if fam else "", model.upper()] if p]
    return " ".join(parts[:3])


if __name__ == "__main__":
    db = sqlite3.connect("C:/Users/Jackb/Downloads/New folder (2)/data/jiji.db",
                         timeout=120, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA busy_timeout=120000;")
    for table in ["warc_item", "listings"]:
        cols = [c[1] for c in db.execute(f"PRAGMA table_info({table})").fetchall()]
        if "normalized_product" not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN normalized_product TEXT")
            db.commit()
        cur = db.execute(f"select rowid, title from {table} where normalized_product is null")
        buf, done = [], 0
        while True:
            rows = cur.fetchmany(500)
            if not rows:
                break
            buf = [(normalize(t), r) for r, t in rows]
            for attempt in range(6):
                try:
                    db.execute("BEGIN")
                    db.executemany(f"update {table} set normalized_product=? where rowid=?", buf)
                    db.execute("COMMIT")
                    break
                except sqlite3.OperationalError:
                    db.execute("ROLLBACK")
                    time.sleep(5)
            done += len(buf)
            print(f"  {table}: {done}", flush=True)
        n = db.execute(f"select count(*) from {table} where normalized_product<>'' and normalized_product is not null").fetchone()[0]
        tot = db.execute(f"select count(*) from {table}").fetchone()[0]
        print(f"{table}: {n}/{tot} = {round(100*n/max(tot,1),1)}%", flush=True)
    print("top products:", db.execute(
        "select normalized_product, count(*) from listings where normalized_product<>'' group by 1 order by 2 desc limit 10").fetchall(), flush=True)
    print("DONE", flush=True)
