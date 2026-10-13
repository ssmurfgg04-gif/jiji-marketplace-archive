"""Back-extract structured attrs from title+description text (cars + land).

Cars: mileage_km (covers '45,000 km', '45000km', '45k km', 'mileage 45,000'),
engine_cc ('1500cc', '1.5L', '1500 cc'). Land/houses: land_unit
(per-acre vs per-plot vs per-sqm) from acre/plot/sqm/50x100/1/8 patterns.

Additive columns on listings (mileage_km, engine_cc, land_unit). Idempotent:
only fills NULL/empty cells. Run: python scripts/attrs_extract.py
"""
import re
import sqlite3

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"

KM = re.compile(r"(\d[\d,]*)\s*(?:km|kms|kilometres|kilometers)\b", re.I)
KM_K = re.compile(r"(\d+(?:\.\d+)?)\s*k\s*kms?\b", re.I)
MILEAGE_BARE = re.compile(r"mileage\s*[:\-]?\s*(\d[\d,]*)", re.I)
CC = re.compile(r"(\d[\d,]*)\s*cc\b", re.I)
LITRE = re.compile(r"\b([0-9]\.\d)\s*(?:l\b|litre)", re.I)
ACRE = re.compile(r"\bacre|\d+\s*ac\b|hectare", re.I)
PLOT = re.compile(r"\bplot|50\s*x\s*100|100\s*x\s*100|1/8|1/4|eighth|quarter acre", re.I)
SQM = re.compile(r"\bsqm|sq\.?\s*m\b", re.I)


def car_attrs(text):
    t = (text or "").replace(",", "")
    mileage, engine = None, None
    m = KM.search(t) or KM_K.search(t)
    if m is None:
        m = MILEAGE_BARE.search(t)
    if m:
        try:
            v = float(m.group(1))
            mileage = int(v * 1000) if "k km" in m.group(0).lower() else int(v)
            if not 1000 <= mileage <= 600000:
                mileage = None
        except Exception:
            pass
    m = CC.search(t)
    if m:
        try:
            v = int(m.group(1))
            engine = v if 500 <= v <= 8000 else None
        except Exception:
            pass
    if engine is None:
        m = LITRE.search(t)
        if m:
            try:
                engine = int(float(m.group(1)) * 1000)
            except Exception:
                pass
    return mileage, engine


def land_unit(text):
    t = (text or "").lower()
    if ACRE.search(t):
        return "per-acre"
    if PLOT.search(t):
        return "per-plot"
    if SQM.search(t):
        return "per-sqm"
    return ""


def main():
    db = sqlite3.connect(DB, timeout=120)
    cols = [c[1] for c in db.execute("PRAGMA table_info(listings)").fetchall()]
    for col in ("mileage_km", "engine_cc", "land_unit"):
        if col not in cols:
            db.execute(f"ALTER TABLE listings ADD COLUMN {col} TEXT")
            print(f"migrated: +{col}")
    n_car = n_land = 0
    rows = db.execute(
        "SELECT guid, title, description, description_full, category FROM listings "
        "WHERE ((lower(category) LIKE '%car%' OR lower(category) LIKE '%vehicle%' "
        "OR lower(category) LIKE '%motor%') AND mileage_km IS NULL) "
        "OR ((lower(category) LIKE '%land%' OR lower(category) LIKE '%house%' "
        "OR lower(category) LIKE '%propert%' OR lower(category) LIKE '%rent%') "
        "AND (land_unit IS NULL OR land_unit=''))").fetchall()
    print(f"candidates: {len(rows)}")
    for guid, title, desc, full, cat in rows:
        blob = f"{title or ''} {desc or ''} {full or ''}"
        c = (cat or "").lower()
        if "car" in c or "vehicle" in c or "motor" in c:
            mileage, engine = car_attrs(blob)
            if mileage or engine:
                db.execute("UPDATE listings SET mileage_km=COALESCE(mileage_km,?), "
                           "engine_cc=COALESCE(engine_cc,?) WHERE guid=?",
                           (mileage, engine, guid))
                n_car += 1
        else:
            u = land_unit(blob)
            if u:
                db.execute("UPDATE listings SET land_unit=? WHERE guid=?", (u, guid))
                n_land += 1
    # warc_item pass (111k rows, same regexes; url-PK table, update by rowid)
    wcols = [c[1] for c in db.execute("PRAGMA table_info(warc_item)").fetchall()]
    for col in ("mileage_km", "engine_cc", "land_unit"):
        if col not in wcols:
            db.execute(f"ALTER TABLE warc_item ADD COLUMN {col} TEXT")
            print(f"migrated warc_item: +{col}")
    w_car = w_land = 0
    wrows = db.execute(
        "SELECT rowid, title, description, category FROM warc_item "
        "WHERE mileage_km IS NULL").fetchall()
    print(f"warc candidates: {len(wrows)}")
    for rowid, title, desc, cat in wrows:
        blob = f"{title or ''} {desc or ''}"
        c = (cat or "").lower()
        if "car" in c or "vehicle" in c or "motor" in c:
            mileage, engine = car_attrs(blob)
            if mileage or engine:
                db.execute("UPDATE warc_item SET mileage_km=COALESCE(mileage_km,?), "
                           "engine_cc=COALESCE(engine_cc,?) WHERE rowid=?",
                           (mileage, engine, rowid))
                w_car += 1
        elif any(k in c for k in ("land", "house", "propert", "rent")):
            u = land_unit(blob)
            if u:
                db.execute("UPDATE warc_item SET land_unit=? WHERE rowid=?", (u, rowid))
                w_land += 1
    print(f"warc filled: {w_car} vehicles, {w_land} property")
    db.commit()
    print(f"filled: {n_car} vehicles (mileage/engine), {n_land} property (unit)")
    for col in ("mileage_km", "engine_cc", "land_unit"):
        print(f"coverage {col}:",
              db.execute(f"SELECT COUNT(*) FROM listings WHERE {col} IS NOT NULL "
                         f"AND {col}!=''").fetchone()[0])


if __name__ == "__main__":
    main()
