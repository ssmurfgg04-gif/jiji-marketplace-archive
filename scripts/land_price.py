"""Location-aware land pricing: size parse + location keys + area medians.

Per the spec: pull county/town/estate from text, merge messy names
(Ruiru/Kamakis/Membley -> one key), use coordinates if present (schema has
none today — text mapping carries it), group by location (+unit type), median
KSh/sqm per area, score each listing vs its OWN area median.

Size grammar: acres (incl 1/8,1/4,1/2,eighth,quarter,hectares), dimensions
AxB in feet (-> sqm), direct sqm. Unsized per-plot rows assume 50x100
(464.5 sqm) with assumed_size=1. Leases excluded (different economics).

Writes listings.price_per_sqm, location_key (+ area stats table land_areas).
Rerunnable; idempotent. Usage: python scripts/land_price.py
"""
import re
import sqlite3
import statistics

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"
SQM_PER_ACRE = 4046.86
SQM_PER_SQFT = 0.092903

DIM = re.compile(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(ft|feet|m\b)?", re.I)
ACRE_N = re.compile(r"(\d+(?:\.\d+)?)\s*acres?\b", re.I)
FRAC = {"1/8": 0.125, "1/4": 0.25, "1/2": 0.5, "eighth": 0.125,
        "quarter": 0.25, "half": 0.5}
HA = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ha\b|hectares?\b)", re.I)
SQM = re.compile(r"(\d[\d,]*)\s*(?:sqm|sq\.?\s*m)\b", re.I)

ALIASES = {"kamakis": "ruiru", "membley": "ruiru", "ruiru": "ruiru",
           "ongata rongai": "ongata rongai", "rongai": "ongata rongai",
           "nairobi central": "nairobi cbd", "nairobi cbd": "nairobi cbd",
           "thika": "thika", "juja": "juja", "limuru": "limuru",
           "kiambu": "kiambu", "karen": "karen", "langata": "langata",
           "westlands": "westlands", "kilimani": "kilimani",
           "kileleshwa": "kileleshwa", "lavington": "lavington",
           "embakasi": "embakasi", "kasarani": "kasarani",
           "kahawa": "kahawa", "githurai": "githurai", "kayole": "kayole",
           "umoja": "umoja", "buruburu": "buruburu", "komarock": "komarock",
           "syokimau": "syokimau", "athi river": "athi river",
           "mlolongo": "mlolongo", "kitengela": "kitengela", "ngong": "ngong",
           "nakuru": "nakuru", "eldoret": "eldoret", "kisumu": "kisumu",
           "mombasa": "mombasa", "ruai": "ruai", "joska": "joska"}

LEASE = re.compile(r"\bleas|\brent\b|\blet\b|\bhiring\b", re.I)


def parse_sqm(text):
    t = (text or "").lower().replace(",", "")
    m = SQM.search(t)
    if m:
        try:
            v = float(m.group(1))
            if 20 <= v <= 1000000:
                return v, 0
        except Exception:
            pass
    m = DIM.search(t)
    if m:
        try:
            a, b, u = float(m.group(1)), float(m.group(2)), (m.group(3) or "ft")
            area = a * b * (1.0 if u.strip().lower().startswith("m") else SQM_PER_SQFT)
            if 20 <= area <= 1000000:
                return area, 0
        except Exception:
            pass
    m = ACRE_N.search(t)
    if m:
        try:
            return float(m.group(1)) * SQM_PER_ACRE, 0
        except Exception:
            pass
    for k, f in FRAC.items():
        if k in t and "acre" in t:
            return f * SQM_PER_ACRE, 0
    m = HA.search(t)
    if m:
        try:
            return float(m.group(1)) * 10000, 0
        except Exception:
            pass
    return None, 0


def location_key(region, title, desc):
    blob = f"{region or ''} {title or ''} {desc or ''}".lower()
    for alias, canon in ALIASES.items():
        if alias in blob:
            return canon
    r = re.sub(r"\s+", " ", (region or "")).strip().lower()
    return r if r else "unknown"


def main():
    db = sqlite3.connect(DB, timeout=120)
    cols = [c[1] for c in db.execute("PRAGMA table_info(listings)").fetchall()]
    for col in ("price_per_sqm", "location_key"):
        if col not in cols:
            db.execute(f"ALTER TABLE listings ADD COLUMN {col} TEXT")
            print(f"migrated: +{col}")
    db.execute("DROP TABLE IF EXISTS land_areas")
    db.execute("""CREATE TABLE land_areas(
      location_key TEXT PRIMARY KEY, n INTEGER, med_per_sqm REAL)""")

    rows = db.execute(
        "SELECT guid, title, price, region, description FROM listings "
        "WHERE (lower(category) LIKE '%land%' OR lower(category) LIKE '%house%' "
        "OR lower(category) LIKE '%propert%') AND price IS NOT NULL AND price > 0").fetchall()
    print(f"property rows with price: {len(rows)}")
    per_area = {}
    scored = 0
    for guid, title, price, region, desc in rows:
        blob = f"{title or ''} {desc or ''}"
        if LEASE.search(blob):
            db.execute("UPDATE listings SET land_unit='lease' WHERE guid=? "
                       "AND (land_unit IS NULL OR land_unit='')", (guid,))
            continue
        sqm, _ = parse_sqm(blob)
        assumed = 0
        if sqm is None and "plot" in blob.lower():
            sqm, assumed = 50 * 100 * SQM_PER_SQFT, 1
        if not sqm:
            continue
        pps = price / sqm
        if not 10 <= pps <= 2000000:
            continue
        key = location_key(region, title, desc)
        per_area.setdefault(key, []).append(pps)
        db.execute("UPDATE listings SET price_per_sqm=?, location_key=? WHERE guid=?",
                   (round(pps, 2), key, guid))
        scored += 1
    for key, vals in per_area.items():
        if len(vals) >= 5:
            db.execute("INSERT INTO land_areas VALUES(?,?,?)",
                       (key, len(vals), round(statistics.median(vals), 2)))
    db.commit()
    print(f"scored: {scored} rows")
    print("--- area medians (KSh/sqm, n>=5) ---")
    for k, n, m in db.execute("SELECT * FROM land_areas ORDER BY med_per_sqm DESC"):
        print(f"  {n:>4} | {m:>10,.0f} | {k}")
    print("--- cheapest vs own area (ratio<60%, area n>=5) ---")
    n = 0
    for guid, title, price, pps, key, med in db.execute(
            """SELECT l.guid, l.title, l.price, l.price_per_sqm, l.location_key, a.med_per_sqm
               FROM listings l JOIN land_areas a ON a.location_key=l.location_key
               WHERE l.price_per_sqm IS NOT NULL AND a.n>=5"""):
        r = float(pps) / med
        if r < 0.60:
            print(f"  {r*100:4.0f}% of {key} med | {price:>10,} | {(title or '')[:60]}")
            n += 1
            if n >= 12:
                break
    print("deals shown:", n)
    db.close()


if __name__ == "__main__":
    main()
