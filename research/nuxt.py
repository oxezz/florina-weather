import json, re, sys

path = r"C:\Users\alex_\.unsloth\studio\sandbox\__LOCALID_VObRaqg\florina-weather\research\emy_home.html"
h = open(path, encoding="utf-8", errors="replace").read()

m = re.search(r'<script type="application/json"[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
if not m:
    print("NOT FOUND"); sys.exit(1)
raw = m.group(1)
data = json.loads(raw)
print("payload len", len(data))


def unflatten(values):
    hydrated = {}

    def hydrate(index):
        if isinstance(index, str):
            return index
        if index == -1:
            return None
        if index == -2:
            return float("nan")
        if index == -3:
            return float("inf")
        if index == -4:
            return float("-inf")
        if index == -5:
            return 0
        if index == -6:
            return -0.0
        if index in hydrated:
            return hydrated[index]
        value = values[index]
        if not isinstance(value, (dict, list)):
            hydrated[index] = value
            return value
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                # devalue reviver wrapper, e.g. ["Reactive", 13985]
                tag = value[0]
                if tag == "EmptyRef":
                    hydrated[index] = None
                    return None
                inner = hydrate(value[1]) if len(value) > 1 else None
                hydrated[index] = inner
                return inner
            arr = []
            hydrated[index] = arr
            for v in value:
                arr.append(hydrate(v))
            return arr
        obj = {}
        hydrated[index] = obj
        for k, v in value.items():
            obj[k] = hydrate(v)
        return obj

    return hydrate(0)


root = unflatten(data)
print("root type", type(root))
out = r"C:\Users\alex_\.unsloth\studio\sandbox\__LOCALID_VObRaqg\florina-weather\research\emy_nuxt.json"
json.dump(root, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("written", out)

# find stations
found = []


def walk(node, path=""):
    if isinstance(node, dict):
        keys = set(node.keys())
        if {"latitude", "longitude"} <= keys or {"lat", "lon"} <= keys:
            found.append(node)
        for k, v in node.items():
            walk(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, path + "/" + str(i))


walk(root)
print("candidate station objects:", len(found))
for s in found:
    nm = s.get("name") or s.get("localName") or s.get("stationName") or s.get("en")
    print(json.dumps({"name": nm, "lat": s.get("latitude"), "lon": s.get("longitude"),
                      "elev": s.get("elevation"), "id": s.get("id"),
                      "wid": s.get("webportalCorrespondingStationId")}, ensure_ascii=False))
