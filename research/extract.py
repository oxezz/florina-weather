import sys, re
from pypdf import PdfReader

path = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else None
r = PdfReader(path)
print(f"PAGES: {len(r.pages)}")
chunks = []
for i, p in enumerate(r.pages):
    try:
        t = p.extract_text() or ""
    except Exception as e:
        t = f"<<ERR {e}>>"
    chunks.append(f"\n===== PAGE {i+1} =====\n{t}")
full = "".join(chunks)
if out:
    with open(out, "w", encoding="utf-8") as f:
        f.write(full)
    print("written", out, len(full))
else:
    print(full[:6000])
