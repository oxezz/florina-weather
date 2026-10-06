# -*- coding: utf-8 -*-
"""Fetch and parse useful data from the EMY old portal (oldportal.emy.gr).
Stores cleaned JSON in emy_data/ for the website to use."""
import os, re, json, urllib.request

UA = {"User-Agent": "Mozilla/5.0"}
BASE = "http://oldportal.emy.gr"
OUT = "emy_data"

def fetch(path):
    req = urllib.request.Request(BASE + path, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")

def strip_ctrl(s):
    return re.sub(r"[\x00-\x1f\x7f]", "", s)

os.makedirs(OUT, exist_ok=True)

# --- 1. Meteoalarm warnings (rich event data: wind, gale, heat, etc.) ---
html = fetch("/emy/en/warning/meteoalarm")
m = re.search(r"meteoalarmJson = (\{.*\});", html, re.S)
raw = m.group(1)
start = raw.find("{")
# value ends right before the ',\n' that separates it from meteoalarmOptions
end = raw.find(",\n", start)
val = raw[start:end]
data = json.loads(strip_ctrl(val))
warnings = data["warnings"]
# keep only the most recent per area/event
seen = {}
for w in warnings:
    key = (w["alert"]["areaName"], w["alert"]["event"])
    if key not in seen:
        seen[key] = w
clean_warnings = list(seen.values())
json.dump(clean_warnings, open(os.path.join(OUT, "warnings.json"), "w", encoding="utf-8"),
        ensure_ascii=False, indent=1)
print("warnings:", len(clean_warnings))

# --- 2. UV index forecast ---
try:
    html = fetch("/emy/en/forecast/deikths_uv")
    # find embedded json or table
    j = re.search(r"uvIndexJson = (\{.*\});", html, re.S)
    if j:
        val = j.group(1)[j.group(1).find("{"):j.group(1).rfind("}")+1]
        uv = json.loads(strip_ctrl(val))
        json.dump(uv, open(os.path.join(OUT, "uv.json"), "w", encoding="utf-8"),
                ensure_ascii=False, indent=1)
        print("uv: parsed json")
    else:
        open(os.path.join(OUT, "uv.html"), "w", encoding="utf-8").write(html)
        print("uv: no json, saved html")
except Exception as e:
    print("uv ERROR:", e)

# --- 3. Current weather observation ---
try:
    html = fetch("/emy/en/observation/sa_xartis_paron_kairos_perioxes")
    open(os.path.join(OUT, "current.html"), "w", encoding="utf-8").write(html)
    print("current: saved html")
except Exception as e:
    print("current ERROR:", e)

# --- 4. Meteogramma (city forecast) ---
html = fetch("/emy/en/forecast/meteogramma_emy?perifereia=West%20Macedonia&poli=Florina")
open(os.path.join(OUT, "meteogramma.html"), "w", encoding="utf-8").write(html)
print("meteogramma: saved html")

print("DONE")
