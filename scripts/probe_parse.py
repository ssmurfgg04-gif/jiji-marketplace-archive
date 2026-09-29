import re, time, json, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jiji-snapshot/1.0"}
URL = "https://jiji.co.ke/computer-hardware?query=ddr3+desktop+ram"

req = urllib.request.Request(URL, headers=UA)
with urllib.request.urlopen(req, timeout=60) as r:
    html = r.read().decode("utf-8", "ignore")
print("bytes:", len(html))

# Jiji embeds listings as JSON in Nuxt state: find title/price/url/seller tuples
# Pattern observed: "title":"...","...price_title":"KSh X..." ... "url":"/nairobi-...html..."
titles = re.findall(r'"title":"([^"\\]{10,120})"', html)
prices = re.findall(r'"price_title":"(KSh [^"]+)"', html)
urls = re.findall(r'"url":"((?:/nairobi|/kiambu|/mombasa|/nakuru|/kisumu|/eldoret)[^"]+?\.html)', html)
sellers = re.findall(r'"user_name":"([^"\\]{2,60})"', html)
print("titles:", len(titles), "prices:", len(prices), "urls:", len(urls), "sellers:", len(sellers))
for t, p in list(zip(titles, prices))[:6]:
    print(f"  {p} | {t[:80]}")
print("sample urls:", urls[:3])
print("sample sellers:", sellers[:5])
