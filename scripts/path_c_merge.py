"""PATH C: merge real mined records (Wayback + Common Crawl) into a unified,
currency-normalized (KES baseline) dataset with temporal feature engineering.

Reads:  listings.tsv (Wayback API/HTML)  and  cc_rows.tsv (Common Crawl HTML)
Writes: jiji_mined_dataset_<ts>.csv  +  .parquet   and   pipeline.json
"""
import csv, os, re, sys, json
from datetime import datetime, date
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.__file__))  # stdlib first (local inspect.py shadow)
import inspect  # cache stdlib inspect BEFORE numpy/pyarrow lazy-imports it

BASE = os.path.dirname(os.path.abspath(__file__))
WB = os.path.join(BASE, "listings.tsv")
CC = os.path.join(BASE, "cc_rows.tsv")

TS = datetime.now().strftime("%Y%m%d_%H%M%S")
CSV_OUT = os.path.join(BASE, "jiji_mined_dataset_%s.csv" % TS)
PKL_OUT = os.path.join(BASE, "jiji_mined_dataset_%s.parquet" % TS)
META_OUT = os.path.join(BASE, "pipeline_metadata_%s.json" % TS)

# ---- currency helpers (regional domains to KES baseline; rates stamped 2026-10-09) ----
# NOTE: gh was 0.11 (KES->GHS direction, 100x under: 650 GHS became 71 KES).
# 1 GHS ~= 11 KES, so GHS->KES multiplies by ~11. Re-verify other crosses yearly;
# et has zero rows so far (unverified rate, flagged not applied blindly).
CURRENCY_TO_KES = {"ke": 1.0, "ng": 0.085, "tz": 0.053, "ug": 0.078, "za": 7.8, "et": 0.028, "gh": 11.0}
CURRENCY_CODE = {"ke": "KES", "ng": "NGN", "tz": "TZS", "ug": "UGX", "za": "ZAR", "et": "ETB", "gh": "GHS"}

def clean_price(raw, country):
    if not raw:
        return None, None
    s = raw.strip()
    s = re.sub(r"[^\d.,]", "", s)  # drop symbols/spaces, keep digits , .
    s = s.replace(",", "")
    try:
        v = float(s)
    except Exception:
        return None, None
    cur = CURRENCY_CODE.get(country, "KES")
    fx = CURRENCY_TO_KES.get(country, 1.0)
    return v, v * fx

def parse_ts(ts):
    if not ts:
        return ""
    t = str(ts).strip()
    if t.isdigit() and len(t) >= 14:
        return f"{t[0:4]}-{t[4:6]}-{t[6:8]}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return t[:10]

def days_between(a, b):
    try:
        fa = date.fromisoformat(a[:10])
        fb = date.fromisoformat(b[:10])
        return (fb - fa).days
    except Exception:
        return None

def load_wb():
    rows = []
    if not os.path.exists(WB):
        return rows
    with open(WB, encoding="utf-8") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for r in rd:
            rows.append(r)
    return rows

def load_cc():
    rows = []
    if not os.path.exists(CC):
        return rows
    with open(CC, encoding="utf-8") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for r in rd:
            rows.append(r)
    return rows

def main():
    wb = load_wb()
    cc = load_cc()
    print(f"loaded: wayback={len(wb)} cc={len(cc)}")

    # Build merged records keyed by guid. WB is richer; CC adds temporal captures.
    merged = OrderedDict()

    def add_cc(r):
        g = (r.get("#guid") or r.get("guid") or "").strip()
        if not g:
            return
        m = merged.setdefault(g, {"guid": g, "title": r.get("title"), "price_raw": r.get("price"),
                                  "first_seen": parse_ts(r.get("capture_ts")),
                                  "last_seen": parse_ts(r.get("capture_ts")),
                                  "country": r.get("country", "ke"),
                                  "captures": [parse_ts(r.get("capture_ts"))],
                                  "prices": [r.get("price")]})
        m["country"] = m["country"] or r.get("country", "ke")
        if r.get("url") and not m.get("url"):
            m["url"] = r["url"]
        if r.get("category_name") and not m.get("category_name"):
            m["category_name"] = r["category_name"]
        m["country"] = m["country"] or r.get("country", "ke")
        cap = parse_ts(r.get("capture_ts"))
        m["captures"].append(cap)
        if r.get("price"):
            m["prices"].append(r.get("price"))
        if cap and (not m["first_seen"] or cap < m["first_seen"]):
            m["first_seen"] = cap
        if cap and (not m["last_seen"] or cap > m["last_seen"]):
            m["last_seen"] = cap

    def add_wb(r):
        g = (r.get("guid") or "").strip()
        if not g:
            return
        m = merged.setdefault(g, {"guid": g, "title": r.get("title"), "price_raw": r.get("price"),
                                  "first_seen": "", "last_seen": "", "country": r.get("country", "ke"),
                                  "captures": [], "prices": [r.get("price")]})
        if r.get("title"):
            m["title"] = r["title"]
        if r.get("price"):
            m["price_raw"] = r["price"]
        if r.get("web_url") or r.get("url"):
            m["url"] = r.get("web_url") or r.get("url")
        # use rich wayback flags as source of truth where available
        fields = ["date_created", "date_moderated", "date_edited", "seller_id", "seller_name",
                  "phone", "category_id", "category_name", "count_views", "fav_count",
                  "adverts_count", "feedback_count", "rating", "boost_badge", "image_urls"]
        for k in fields:
            if r.get(k):
                m[k] = r[k]
        m["source_wayback"] = "1"
        m["capture_ts"] = parse_ts(r.get("capture_ts"))
        if r.get("country"):
            m["country"] = r["country"]

    for r in wb:
        add_wb(r)
    for r in cc:
        add_cc(r)

    print(f"merged unique GUIDs: {len(merged)}")

    # ---- feature engineering ----
    out_cols = ["guid", "title", "price", "price_local_currency", "currency", "price_kes",
                "country", "category_name", "first_seen", "last_seen", "capture_count",
                "days_listed", "price_first", "price_last", "price_min", "price_max",
                "price_delta", "price_pct_change", "relative_price", "has_images",
                "image_url_count", "seller_id", "seller_name", "count_views", "fav_count",
                "boost_badge", "rating", "adverts_count", "feedback_count", "phone",
                "source_cc", "source_wayback", "url", "unverified", "price_disputed"]
    records = []
    stats = {"ke": 0, "ng": 0, "tz": 0, "ug": 0, "za": 0, "et": 0, "gh": 0, "other": 0}
    n_price = 0
    n_multi = 0

    for g, m in merged.items():
        country = m.get("country") or "ke"
        stats[country] = stats.get(country, 0) + 1
        price_local, price_kes = clean_price(m.get("price_raw"), country)

        prices = []
        for p in m.get("prices") or []:
            pl, pk = clean_price(p, country)
            if pl is not None:
                prices.append(pl)
        if prices:
            n_price += 1
        captures = sorted({c for c in (m.get("captures") or []) if c})
        cap_count = len(captures)
        first_seen = m.get("first_seen") or (captures[0] if captures else "")
        last_seen = m.get("last_seen") or (captures[-1] if captures else "")
        date_created = m.get("date_created") or ""
        base_ts = date_created or first_seen
        days_listed = days_between(base_ts, last_seen) if (base_ts and last_seen) else None

        price_first = prices[0] if prices else None
        price_last = prices[-1] if prices else None
        price_min = min(prices) if prices else None
        price_max = max(prices) if prices else None
        price_delta = (price_last - price_first) if (price_first is not None and price_last is not None) else None
        price_pct = ((price_delta / price_first) * 100) if (price_delta is not None and price_first) else None

        if price_last is not None and len(prices) > 1:
            n_multi += 1

        img_urls = (m.get("image_urls") or "").strip()
        has_images = "1" if len(img_urls) > 3 else ("0" if img_urls == "" else "1")
        img_count = len([x for x in img_urls.split(";") if x]) if img_urls else 0

        rec = {
            "guid": g, "title": m.get("title") or "", "price": price_local,
            "price_local_currency": price_local, "currency": CURRENCY_CODE.get(country, "KES"),
            "price_kes": price_kes, "country": country, "category_name": m.get("category_name") or "",
            "first_seen": first_seen, "last_seen": last_seen, "capture_count": cap_count,
            "days_listed": days_listed, "price_first": price_first, "price_last": price_last,
            "price_min": price_min, "price_max": price_max, "price_delta": price_delta,
            "price_pct_change": round(price_pct, 3) if price_pct is not None else None,
            "relative_price": (price_last / ((price_min + price_max) / 2)) if (price_last and price_min is not None and price_max and (price_min + price_max)) else None,
            "has_images": has_images, "image_url_count": img_count,
            "seller_id": m.get("seller_id") or "", "seller_name": m.get("seller_name") or "",
            "count_views": m.get("count_views") or "", "fav_count": m.get("fav_count") or "",
            "boost_badge": m.get("boost_badge") or "", "rating": m.get("rating") or "",
            "adverts_count": m.get("adverts_count") or "", "feedback_count": m.get("feedback_count") or "",
            "phone": m.get("phone") or "", "source_cc": "1" if g in cc_guids else "",
            "source_wayback": m.get("source_wayback") or "",
            "url": m.get("url") or "",
            # 1 = no seller contact on record: price not callable, treat bait-prone
            # round-number clusters (5000/8000/...) with extra skepticism.
            "unverified": "1" if not (m.get("phone") or m.get("seller_id")) else "",
            # 1 = min/max differ >3x AND both archive sources present: almost
            # certainly a cross-source parse artifact (WB vs CC price_raw for the
            # same guid), not a real repricing. See findings: 80% of multi-capture
            # rows "change" with median +0.1% and +-hundreds-% tails.
            "price_disputed": ("1" if (price_min and price_max and price_min > 0
                                       and price_max / price_min > 3
                                       and m.get("source_wayback")
                                       and g in cc_guids) else ""),
        }
        records.append(rec)

    ke_records = [r for r in records if (r.get("country") or "ke") == "ke"]

    # ---- write CSV (full + ke-only convenience slice) ----
    with open(CSV_OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k) for k in out_cols})
    ke_csv = CSV_OUT.replace(".csv", "_ke_only.csv")
    with open(ke_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        for r in ke_records:
            w.writerow({k: r.get(k) for k in out_cols})

    # ---- write parquet (numpy + pyarrow, no pandas) ----
    try:
        import numpy as np, pyarrow as pa, pyarrow.parquet as pq
        arrays = {}
        for col in out_cols:
            series = [r.get(col) for r in records]
            arrays[col] = series
        tbl = pa.table(arrays)
        pq.write_table(tbl, PKL_OUT)
        wrote_pq = True
    except Exception as e:
        wrote_pq = False
        print("parquet skipped:", e)

    meta = {
        "generated_at": datetime.now().isoformat(),
        "record_count": len(records),
        "sources": {"wayback_rows": len(wb), "common_crawl_rows": len(cc)},
        "unique_guids": len(merged),
        "with_price": n_price,
        "multi_capture": n_multi,
        "countries": stats,
        "columns": out_cols,
        "files": {"csv": os.path.basename(CSV_OUT), "parquet": os.path.basename(PKL_OUT) if wrote_pq else None},
    }
    with open(META_OUT, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)

    print("records:", len(records), "| ke_only:", len(ke_records))
    print("countries:", stats)
    print("with_price:", n_price, "multi_capture:", n_multi)
    print("wrote:", CSV_OUT, "| ke:", ke_csv, "| parquet:", wrote_pq)
    print("meta:", META_OUT)

cc_guids = set()  # filled below (kept as module var referenced above)

if __name__ == "__main__":
    cc_guids = {((r.get("#guid") or "").strip()) for r in load_cc()}
    main()