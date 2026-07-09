import asyncio
import json
from ai.grobid_client import extract_via_grobid
from ai.pdf_analysis import analyze_uploaded_paper

async def run():
    with open('2209.11154v3.pdf', 'rb') as f:
        pdf_bytes = f.read()

    print("Running extract_via_grobid directly...")
    res = await extract_via_grobid(pdf_bytes)
    print("Confidence:", res["confidence"])
    print("Sections keys:", list(res["sections"].keys()))
    if res["abstract"]:
        print("Abstract found! Length:", len(res["abstract"]))
    else:
        print("Abstract is empty.")

if __name__ == "__main__":
    asyncio.run(run())
