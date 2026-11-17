# Jiji Marketplace Archive — Lessons & Findings

A living ledger of what this data has taught us. Sources: `jiji.db`
(topic-scraped Jiji listings via the polite in-and-out collector; the
Common Crawl archive layer if used; `price_history` for move rates),
and targeted market research.

**Numbers are as-observed — used-laptop prices in Nairobi cluster meaningfully,
not perfectly. Always verify by inspecting the unit; this ledger is a map, not a
trade recommendation.**

---

## 1) What the market research says

### Global refurbished PC market (Africa = named key hub in 4 separate reports)
- Grandview / MordorIntelligence / TBRC / ResearchAndMarkets all size 2026 global
  refurbished-laptops at ≈ $8B-12B, growing 5–10% CAGR to 2030-2035.
- Refurbished laptops hold 73.5% device share of the broader refurbished market.
- Grade A leads at 52% of grade distribution (2025).
- Market drivers: Africa cited as "key demand hub" — affordability + school/
  clinic/NGO procurement RFPs (Atos Group 2025: remanufactured laptops are now
  part of corporate employee-device refresh RFPs).
- Sources: TBRC refurbished laptop report (pp 2025-2030), Mordor Intelligence
  (5.6.5.2 Africa segment), ResearchAndMarkets, Grand View Research.

### Kenya refurbished-market microstructure
- **EX-UK imports drive the premium end** (vendor: Buytec, UltraBooks, Shah
  Tech): EliteBook 840 G5/G6 Grade-A ≈ KSh 28,000-36,000 with 3-6mo warranty.
- Circular seller spectrum: Jiji individual ad-hoc sellers < domestic plumbed
  shop trade ≈ from-KSh 25,000 < import-grade ~KSh 50,000 < new ~KSh 60,000+.
- Market is thinner than US/EU; the shop premium for reassurance (warranty,
  testing, returns) is the **differentiator**, not list price (Jiji undercuts
  street shops by KSh 10-15k routinely).
- Jumia Kenya stocks 5,298 refurbished laptop listings today.
- Source reports: Vellum News (Jan 2026), JijiPricing benchmarks from archive.

### Aligned-think / warnings from second-hand scholarship
- Price consciousness & frugality (Yan 2015, Padmavathy 2019) and the price-
  negotiability gap drive P/U demand — second-hand is a rational economic
  choice, not defect-averse cheat.
- Specialist vs generalist resellers: specialists fear "ex-UK" stock; generalists
  avoid missed profit (PMC 2026: "Settling Secondhand Sales…").
- Reselling tossed-laptops arbitrage has natural ceiling: the market exists, not
  the free lunch — information asymmetry and relationships with corporate-IT
  managers win > macro-scalp plays.

---

## 2) What the archive shows — concrete pricing, by defect type

The following are extractions across `~/jiji-marketplace-archive/data/jiji.db`,
listings with descriptions in `description`:

### 2.1 — Defect-driven discount bands (Nairobi/Mokte × below, 2026-09→11)
Order of severity — more defect = deeper discount:

| Price tier | Example listings | Comment |
|---|---|---|
| **KSh 5,000 - 6,000 (broken/marginal)** | Asus A42Jr (BOOT board dead) KSh 5,000; Lenovo G500 screen damaged; HP Pavilion 13 (K&B dead); HP ProBook 430 G2 i7 (K&B dead); Acer Aspire 1 (dead battery only) | Dead battery easiest to replace = cheapest fix, best ratio. Dead keyboard+Battery ~KSh 3-5K fix. |
| **KSh 8,000 - 10,000 (single light defect)** | Dell E6410 (keyboard keys, battery missing) KSh 8,000; Ideapad 110 (battery only) KSh 10,000; Dell E4300 (K&B) KSh 10,000; "I Am Selling my Laptop" (keyboard disconnected) KSh 9,600 | Pre-purchase items. Quick checks: power on, verify CPU-rank, confirm battery or not present. |
| **KSh 11,000 - 15,000 (X1 Carbon / T480 class)** | ThinkPad X1 Carbon i7 — KSh 11,000 (touch + backlit, "in perfect"); T480 — KSh 14,000 (Zimmerman — the one we bought); EliteBook 840 G2 — KSh 15,000; Latitude 7490 touch — KSh 15,000 | Mid-tier "everything works except X" listing; aggressive sellers → real win if condition matches. |
| **KSh 16,000 - 18,050 (repairable i7/i5 gen7)** | Surface Pro 7 i5 — KSh 16,000 ("Shows logo then off"); Toshiba Portege Z30 — KSh 16,000; EliteBook 850 G1 i7 — KSh 18,000 | Special pricing for clear-fault or non-fixable units; strong candidate for fix-flair + ₹3-5K repair. |
| **KSh 22,000 - 35,000 ("good condition, why selling")** | ThinkPad X390 i5 — KSh 18,500; EliteBook 830 G6 — KSh 27,000; Dell Latitude 5410 — KSh 24,500; Toshiba Tecra X40 — KSh 20,000 | Avoids post-warranty obvious dump; genuinely hard to flip further; market clear prices. |
| **KSh 50,000+ ("rare but discounted by descms")** | MacBook Air 2020 M1 — KSh 55,000 (trackpad lag) | Exotic asks discounted by single visible defect ≈ nearest to over-discount to median is here. |

### 2.2 — Tobule biased item patterns
- MacBook/fixed-risk line: KSh 135K - 339K — trackpad lag, battery cycle-count,
  liquid, M%/M-class — single-defect worth KSh 15-30K discount = best mover-
  per-hour window but lowest velocity.
- KSh 5-15K units = highest turnover velocity; listings can disappear within
  hours (e.g. Zim listing disappear in <30min from posting — turns=round-the-clock
  for the buyer when pricing is honored).

### 2.3 — Competitive intel for armed coverage
- Jumia undercut & \*\*Jiji sub\*\*: "3 months warranty + sealed original box"
  bullet points = Jiji asori no warranty = use Jiji's defect list for margin, not
  assurance..
- Back-flip margins: KSh 5-10K defect unit + KSh 3-4K fix = KSh 18-25K sale.

---

## 3) Practical buy-list (data-driven, current)

Ranked by expected return:

| Drop-in | Why | Composite advise |
|---|---|---|
| HP Pavilion 13 i3 — KSh 5,500 | K&B dead; plenty of them pop back fully restored | Buy + KSh 1.5K keyboard |
| HP ProBook 430 G2 i7 — KSh 5,999 | i7 gen ≤5th — keyboard+battery | KSh 2K parts = KSh 13-14K flip |
| Lenovo G500 Celeron — KSh 5,000 | Screen damaged; still runs | External monitor only — hobby flipper only |
| Dell E6410 i5 — KSh 8,000 | Missing battery, 4 dead keys | KSh 3.5K battery fix = KSh 11-12K sale |
| ThinkPad X1 Carbon i7 — KSh 11,000 | Touch + backlit, "perfect" listing | Fast flip if genuine —verify |
| Surface Pro 7 i5 — KSh 16,000 | "Logo then off" | ASP issue fix ≈ KSh 3-8K repair = 2× sale ceiling |
| EliteBook 840 G2 — KSh 15,000 | 5+6 keys dead, gen6, listing mismatched | Cheap conversion for KSh 18-20K |
Side ZIP (CPU-confirmed NOT these):
- KSh 6,000 "HP EliteBook 840 G5 16GB i5" — request more shots or walk (the
  seller is explicit — he's selling parts not a machine).
- MacBook Air M1 KSh 55,000 (trackpad lag) — price is right-ish for a working
  Mac rice with sl effectiveMac discount; skip for cash-conversion speed.
- Surface Pro 7 — KSh 16,000 (does not boot) — takes bios-flush + ssd swap.
  High risk. Only if you already own the Sandler board fix.

---

## 4) Lesson learned about the archive itself

1. **Data ≠ declared-condition market rate.** ~
   Listings often advertise "in perfect condition" while the body describes
   defects — the archive's description mining finds keywords more reliably
   than the top-line headline pricing.
2. **The low-ticket tier is the highest-velocity, no-return, genuine-buyer
   sweet spot.** Early oddities (Zimmerman T480 missed the seller by <30min
   between listings) are the best triangulation of fact-driven pickups.
3. **Counter-intuition: the "good-mystery macbook" approach yields a smaller
   bigger pot, but more distance between defects-for-sale and realizable
   resale.** Stick with Known-good X1 Carbon N-carbon-widths or Dell Latitudes
   for the flip game; MacBook sparingly.
4. **Local-regional price spread exists and persists:**
   - Cheap-boys spectacle-first listings tend to cluster Nairobi Central & Ruiru;
   - Sustainability deletes (largest floods of battery/keys) in Ruiru & Kisii;
   - Whole units with truly rare parts (2018-22 macs; Surface flat-screen) cluster in mature-Nairobi & Westlands shops (warranty included = ask).
5. **This archive is notion-accurate for what sellers *inquire*, not what sellers
   *offer*.** ⚠ Verify all KSh 10K-and-below listings in person — no match
   on "brand new price 18,000" or similar mis-sets (scam-pattern, bait and switch).
---

Last verified: 2026-11-17 15:45 EAT. Contributors: opencode/jiji-archive learning sessions.
