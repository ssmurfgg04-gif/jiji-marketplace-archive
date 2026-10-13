"""Market test battery (runs the high-payoff analyses from the research agenda).

Reads mined parquet (history) + live jiji.db (present). Prints tables, not
papers. FX anchors are APPROXIMATE annual averages (unverified, for direction
not precision) — replace with CBK series when joined.

Usage: python scripts/market_tests.py [--fx|--depreciation|--regional|--events|--counts2026|--all]
"""
import argparse
import csv
import statistics
import sys
import os


def load_fx():
    fx, src, cpi = {}, {}, {}
    try:
        with open("data/fx_rates.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                y = int(row["year"])
                fx[y] = float(row["usd_kes"])
                src[y] = row["usd_kes_source"]
                if row.get("cpi_2010eq100"):
                    cpi[y] = float(row["cpi_2010eq100"])
    except Exception:
        fx = {2019: 101.99, 2026: 129.78}
        src = {y: "builtin-fallback" for y in fx}
        cpi = {}
    return fx, src, cpi


FX, FX_SRC, CPI = load_fx()
FX_APPROX = {y for y, s in FX_SRC.items() if "approx" in s or "builtin" in s}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize_product import normalize


def load_mined():
    import pyarrow.parquet as pq
    import glob
    files = sorted(glob.glob("jiji_mined_dataset_*.parquet"))
    if not files:
        print("no mined parquet found")
        sys.exit(1)
    print("mined file:", files[-1])
    return pq.read_table(files[-1]).to_pylist()


def test_fx(rows):
    print("=== 1. FX PASS-THROUGH: yearly median KES (+USD-equiv) vs USDKES ===")
    print("--- phones (iphone+galaxy s, KE) ---")
    by_year = {}
    for r in rows:
        if r.get("country") != "ke" or not r.get("price_kes"):
            continue
        n = normalize(r.get("title") or "")
        if n["kind"] != "phone" or n["family"] not in ("iphone", "galaxy s"):
            continue
        fs = (r.get("first_seen") or "")[:4]
        if fs.isdigit() and 2019 <= int(fs) <= 2026 and 1000 < r["price_kes"] < 500000:
            by_year.setdefault(int(fs), []).append(r["price_kes"])
    for y in sorted(by_year):
        v = by_year[y]
        if len(v) < 20:
            continue
        m = statistics.median(v)
        real = f"{m * CPI[2019] / CPI[y]:>9,.0f}" if y in CPI and 2019 in CPI else "      n/a"
        print(f"  {y} n={len(v):>5} medKES={m:>9,.0f} real2019={real} medUSD~{m/FX.get(y, 130):>7,.0f} fx~{FX.get(y)}")
    print("--- laptops (KE) ---")
    by_year = {}
    for r in rows:
        if r.get("country") != "ke" or not r.get("price_kes"):
            continue
        if "laptop" not in (r.get("title") or "").lower():
            continue
        fs = (r.get("first_seen") or "")[:4]
        if fs.isdigit() and 2019 <= int(fs) <= 2026 and 5000 < r["price_kes"] < 500000:
            by_year.setdefault(int(fs), []).append(r["price_kes"])
    for y in sorted(by_year):
        v = by_year[y]
        if len(v) < 20:
            continue
        m = statistics.median(v)
        real = f"{m * CPI[2019] / CPI[y]:>9,.0f}" if y in CPI and 2019 in CPI else "      n/a"
        print(f"  {y} n={len(v):>5} medKES={m:>9,.0f} real2019={real} medUSD~{m/FX.get(y, 130):>7,.0f} fx~{FX.get(y)}")


def test_depreciation(rows):
    print("=== 3. USED-CAR DEPRECIATION (KE, within top make-models) ===")
    fams = {}
    for r in rows:
        if r.get("country") != "ke" or not r.get("price_kes"):
            continue
        n = normalize(r.get("title") or "")
        if n["kind"] != "car" or not n["year"] or not n["family"]:
            continue
        cap = int((r.get("first_seen") or "2021")[:4] or 2021)
        age = cap - n["year"]
        if 0 <= age <= 20 and 200000 < r["price_kes"] < 15000000:
            fams.setdefault((n["brand"], n["family"]), []).append((age, r["price_kes"]))
    top = sorted(fams.items(), key=lambda kv: -len(kv[1]))[:6]
    for (b, f), v in top:
        buckets = {}
        for age, p in v:
            buckets.setdefault(min(age // 3 * 3, 15), []).append(p)
        curve = " ".join(f"{a}-{a+2}:{statistics.median(p):,.0f}(n={len(p)})"
                         for a, p in sorted(buckets.items()) if len(p) >= 5)
        print(f"  {b} {f} (n={len(v)}): {curve}")


def test_regional():
    import sqlite3
    print("=== 4. REGIONAL DISPERSION: phone medians by city (live DB) ===")
    db = sqlite3.connect("data/jiji.db")
    cities = ["nairobi", "mombasa", "kisumu", "nakuru", "eldoret"]
    for city in cities:
        ps = [r[0] for r in db.execute(
            "SELECT price FROM listings WHERE category='Mobile Phones' AND price>5000 "
            "AND price<300000 AND lower(region) LIKE ?", ("%" + city + "%",))]
        if len(ps) >= 10:
            print(f"  {city:9} n={len(ps):>4} med={statistics.median(ps):>9,.0f}")
        else:
            print(f"  {city:9} n={len(ps):>4} (thin)")


def test_events(rows):
    print("=== 2. EVENT WINDOWS: weekly listing counts (mined first_seen) ===")
    from datetime import date
    weeks = {}
    for r in rows:
        fs = (r.get("first_seen") or "")[:10]
        try:
            d = date.fromisoformat(fs)
        except Exception:
            continue
        w = f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
        weeks[w] = weeks.get(w, 0) + 1
    for tag, w in [("2023 Finance Act (assent 2023-06-26)", ["2023-W24", "2023-W25", "2023-W26", "2023-W27", "2023-W28"]),
                   ("2024 Finance Bill protests (June 2024)", ["2024-W22", "2024-W23", "2024-W24", "2024-W25", "2024-W26"])]:
        print(f"  {tag} (CAVEAT: 2024 titles blank, volumes only):")
        for x in w:
            print(f"    {x}: {weeks.get(x, 0)}")


def test_counts2026():
    import sqlite3
    import glob
    print("=== 2026 ROW COUNTS ===")
    db = sqlite3.connect("data/jiji.db")
    print("live listings:", db.execute("SELECT COUNT(*) FROM listings").fetchone()[0])
    print("live with 2026 first_seen:",
          db.execute("SELECT COUNT(*) FROM listings WHERE first_seen LIKE '2026%'").fetchone()[0])
    print("price_history:", db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0])
    print("warc_item:", db.execute("SELECT COUNT(*) FROM warc_item").fetchone()[0])
    print("snapshots:", sorted(g.split("-snapshot-")[-1] for g in glob.glob("data/snapshot-*.parquet")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx", action="store_true")
    ap.add_argument("--depreciation", action="store_true")
    ap.add_argument("--regional", action="store_true")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--counts2026", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    run_all = a.all or not any([a.fx, a.depreciation, a.regional, a.events, a.counts2026])
    need_mined = run_all or a.fx or a.depreciation or a.events
    rows = load_mined() if need_mined else None
    if run_all or a.fx:
        test_fx(rows)
    if run_all or a.depreciation:
        test_depreciation(rows)
    if run_all or a.regional:
        test_regional()
    if run_all or a.events:
        test_events(rows)
    if run_all or a.counts2026:
        test_counts2026()


if __name__ == "__main__":
    main()
