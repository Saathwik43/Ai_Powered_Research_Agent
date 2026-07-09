import urllib.request
import json
from ai.pdf_structure import extract_structure

url = "https://arxiv.org/pdf/2209.11154.pdf"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as response:
    pdf_bytes = response.read()

res = extract_structure(pdf_bytes)
print("Confidence:", res["confidence"])
print("Sections keys:", list(res["sections"].keys()))
for k, v in res["sections"].items():
    print(f"--- {k} ---")
    print(v[:100].replace('\n', ' '))
