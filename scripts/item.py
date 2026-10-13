"""On-demand full enrichment for a listing: full seller description,
listing date, view count via api_web/v1/item/{guid}.

Usage:
  python scripts/item.py <guid> [guid ...]
  python scripts/item.py --flagged --max 50     # enrich all fault-flagged rows lacking full text
  python scripts/item.py --query "thinkpad x230" --max 20

Polite: 1 worker, 2s delay. Writes description_full/date_created/views.
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

BASE = "C:/Users/Jackb/Downloads/New folder (2)"
DB = BASE + "/data/jiji.db"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jiji-research-enrich/1.0"}


def fetch(guid):
    req = urllib.request.Request(f"https://jiji.co.ke/api_web/v1/item/{guid}",
                                 headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except Exception as e:
        print(f"  fetch fail {guid}: {str(e)[:80]}")
        return None


def enrich(db, guid):
    j = fetch(guid)
    if not j:
        return False
    adv = j.get("advert", {}) or {}
    s = j.get("seller", {}) or {}
    desc = adv.get("description", "") or ""
    created = adv.get("date_created", "") or ""
    views = adv.get("count_views", "")
    views = str(views) if views != "" else None
    cond = ""
    for attr in adv.get("attrs") or []:
        if isinstance(attr, dict) and attr.get("name") == "Condition":
            cond = attr.get("value") or ""
            break
    seller = s.get("name", "") or ""
    if not cond:
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from flagged import infer_condition
            cond = infer_condition(f"{adv.get('title', '')} {desc}")
        except Exception:
            cond = ""
    # structured attrs backfill (cars: mileage/engine, property: unit)
    _mileage = _engine = _lunit = None
    try:
        import sys as _sys2, os as _os2
        _sys2.path.insert(0, _os2.path.dirname(_os2.path.abspath(__file__)))
        from attrs_extract import car_attrs, land_unit
        blob = f"{adv.get('title', '')} {desc}"
        _mileage, _engine = car_attrs(blob)
        _lunit = land_unit(blob)
    except Exception:
        pass
    db.execute("UPDATE listings SET description_full=?, date_created=?, views=?, "
               "condition=COALESCE(NULLIF(condition,''),?), "
               "seller=COALESCE(NULLIF(seller,''),?), "
               "mileage_km=COALESCE(mileage_km,?), engine_cc=COALESCE(engine_cc,?), "
               "land_unit=COALESCE(NULLIF(land_unit,''),?) WHERE guid=?",
               (desc, created, views, cond or None, seller or None,
                _mileage, _engine, _lunit or None, guid))
    if views and str(views).isdigit():
        from datetime import datetime, timezone
        db.execute("INSERT OR IGNORE INTO views_history VALUES(?,?,?)",
                   (guid, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    int(views)))
    db.commit()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("guids", nargs="*")
    ap.add_argument("--flagged", action="store_true")
    ap.add_argument("--missing", action="store_true",
                    help="all tech-category rows missing condition or seller")
    ap.add_argument("--query", default=None)
    ap.add_argument("--max", type=int, default=50)
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    db = sqlite3.connect(DB, timeout=120, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA busy_timeout=120000;")
    _cols = [c[1] for c in db.execute("PRAGMA table_info(listings)").fetchall()]
    for _col in ("description", "description_full", "date_created", "views",
                 "mileage_km", "engine_cc", "land_unit"):
        if _col not in _cols:
            db.execute(f"ALTER TABLE listings ADD COLUMN {_col} TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS views_history(
      guid TEXT, seen_at TEXT, views INTEGER,
      PRIMARY KEY (guid, seen_at))""")

    if args.flagged:
        sys.path.insert(0, BASE + "/scripts")
        from flagged import FAULT_PATTERNS, like_clause
        where, params = like_clause(FAULT_PATTERNS)
        guids = [r[0] for r in db.execute(
            f"SELECT guid FROM listings WHERE description_full IS NULL AND ({where})",
            params).fetchall()][:args.max]
    elif args.query:
        guids = [r[0] for r in db.execute(
            "SELECT guid FROM listings WHERE description_full IS NULL "
            "AND lower(title) LIKE ? LIMIT ?",
            ("%" + args.query.lower() + "%", args.max)).fetchall()]
    elif args.missing:
        tech = ("(lower(category) like '%computer%' or lower(category) like '%laptop%' "
                "or lower(category) like '%phone%' or lower(category) like '%tablet%' "
                "or lower(category) like '%car%' or lower(category) like '%vehicle%' "
                "or lower(category) like '%motor%' or lower(category) like '%house%' "
                "or lower(category) like '%land%' or lower(category) like '%rent%' "
                "or lower(category) like '%propert%' or lower(category) like '%pet%' "
                "or lower(category) like '%dog%' or lower(category) like '%cat%')")
        guids = [r[0] for r in db.execute(
            f"SELECT guid FROM listings WHERE {tech} AND "
            "description_full IS NULL AND "
            "((condition is null or condition='') or (seller is null or seller='') "
            "or (date_created is null or date_created='')) "
            "LIMIT ?", (args.max,)).fetchall()]
    else:
        guids = args.guids[:args.max]
    print(f"enriching {len(guids)} items")
    ok = 0
    for g in guids:
        if enrich(db, g):
            ok += 1
        time.sleep(args.delay)
    print(f"done: {ok}/{len(guids)}")


if __name__ == "__main__":
    main()
