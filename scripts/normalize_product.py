"""Product entity resolution: title -> (brand, family, model, storage_gb, year).

Why: normalized_product is blank for 76% of rows. Title->model/year/storage
unlocks every downstream test (FX pass-through, depreciation, regional comps,
CRSP comparison). Phones + cars first (best-structured titles); extend per
category as needed.

Usage:
  from normalize_product import normalize
  normalize("Samsung Galaxy S22 5G 128 GB Black")
  # -> {'brand':'samsung','family':'galaxy s','model':'s22','storage_gb':128,'year':None,...}
  python scripts/normalize_product.py --demo   # sanity table
"""
import re

PHONE_BRANDS = ["apple", "samsung", "xiaomi", "redmi", "poco", "tecno", "infinix",
                "huawei", "oppo", "vivo", "realme", "oneplus", "nokia", "honor",
                "motorola", "lenovo", "itel", "zte", "google"]
CAR_MAKES = ["toyota", "nissan", "honda", "mazda", "subaru", "mitsubishi",
             "volkswagen", "mercedes", "bmw", "audi", "ford", "suzuki", "isuzu",
             "lexus", "land rover", "range rover", "peugeot", "hyundai", "kia",
             "volvo", "porsche", "jeep", "tesla", "byd", "chery", "haval"]


def _storage_gb(t):
    ms = re.findall(r"(\d+)\s*(gb|tb)\b", t)
    if not ms:
        return None
    vals = [int(v) * (1024 if u == "tb" else 1) for v, u in ms]
    # phone storage is the largest GB figure <= 1024; car "500gb" never occurs
    cands = [v for v in vals if v <= 1024]
    return max(cands) if cands else None


def _year(t):
    ms = re.findall(r"\b(19[89]\d|20[0-3]\d)\b", t)
    return int(ms[0]) if ms else None


def normalize(title):
    t = (title or "").lower()
    out = {"brand": "", "family": "", "model": "", "storage_gb": _storage_gb(t),
           "year": _year(t), "kind": ""}
    for b in PHONE_BRANDS:
        if b in t:
            out["brand"] = b
            out["kind"] = "phone"
            break
    if out["kind"] == "phone":
        m = re.search(r"(iphone\s+\d+[a-z ]*?(?:pro max|pro|plus|mini|ultra)?|"
                      r"galaxy\s+(s\d+\s?(?:ultra|plus|fe)?|note\s?\d+[^,]*?|"
                      r"a\d+|z\s?flip\s?\d*|z\s?fold\s?\d*)|"
                      r"(?:redmi\s+note\s+\d+\s?\w*|poco\s+\w+\d*\s?\w*|"
                      r"pixel\s+\d+\s?\w*|camon\s+\d+\s?\w*))", t)
        if m:
            out["model"] = re.sub(r"\s+", " ", m.group(0)).strip()
        out["family"] = ("iphone" if out["brand"] == "apple"
                         else "galaxy s" if "galaxy s" in t
                         else "galaxy" if "galaxy" in t else out["brand"])
        return out
    for mk in CAR_MAKES:
        if mk in t:
            out["brand"] = mk
            out["kind"] = "car"
            rest = t.split(mk, 1)[1].strip()
            mh = re.match(r"([a-z0-9][a-z0-9 \-/]{1,28}?)(?:\s+\d{4}|\s*,|\s*$|\s+for\b|\s+\d+\s*(?:cc|km))", rest)
            out["model"] = (mh.group(1).strip() if mh else rest[:24].strip())
            out["family"] = out["model"].split()[0] if out["model"] else ""
            return out
    return out


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        for t in ["Samsung Galaxy S22 5G 128 GB Black",
                  "Apple iPhone 16 Pro 512 GB Purple",
                  "Xiaomi Redmi Note 14 Pro+ 5G 256 GB Black",
                  "Toyota Corolla Fielder 2018 1500cc",
                  "Nissan Note 2017 DIG-S",
                  "Brand New Fish Bowl"]:
            print(f"{t[:48]:48} -> {normalize(t)}")
