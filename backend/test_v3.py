from ai.pdf_structure import extract_structure
import json

with open('2209.11154v3.pdf', 'rb') as f:
    pdf_bytes = f.read()

res = extract_structure(pdf_bytes)
print("Confidence:", res["confidence"])
print("Sections keys:", list(res["sections"].keys()))
