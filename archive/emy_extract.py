# -*- coding: utf-8 -*-
import re, json, urllib.request
UA = {"User-Agent": "Mozilla/5.0"}
def fetch(path):
    req = urllib.request.Request("http://oldportal.emy.gr" + path, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")

html = fetch("/emy/en/warning/meteoalarm")
m = re.search(r"meteoalarmJson = (\{.*\});", html, re.S)
raw = m.group(1)
opts = raw.find("meteoalarmOptions")
seg = raw[raw.find("{", 0):opts]
# find the exact end: the meteoalarmJson value ends with '}}' pattern before ', meteoalarmOptions'
# Search backward from opts for the '}}' that closes warnings array + object
idx = seg.rfind("}}")
val = seg[:idx]
val = val.replace("\t", " ").replace("\n", " ").replace("\r", " ")
data = json.loads(val)
print("warnings:", len(data["warnings"]))
with open("emy_data/warnings.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
print("saved warnings.json")

# Print summary
from collections import Counter
ev = Counter(); areas = Counter()
for w in data["warnings"]:
    ev[w["alert"]["event"]] += 1
    for area in w["alert"]["info"]:
        for a in area["area"]:
            areas[a["areaDesc"]] += 1
print("events:", dict(ev))
print("=== areas (top 15) ===")
for a, c in areas.most_common(15):
    print(f"  {c:3d}  {a}")

# Show Florina-relevant (West Macedonia / Florina) warnings
print("\n=== Florina-area warnings ===")
for w in data["warnings"]:
    for area in w["alert"]["info"]:
        for a in area["area"]:
            if "Florina" in a["areaDesc"] or "Florin" in a["areaDesc"] or "Macedonia" in a["areaDesc"]:
                print(f"  {w['alert']['event']} | {a['areaDesc']} | {w['alert']['headline']}")
                print(f"     {w['alert']['description'][:120]}")
