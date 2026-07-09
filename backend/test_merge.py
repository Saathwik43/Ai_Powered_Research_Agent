import asyncio
from ai.grobid_client import extract_via_grobid
from ai.pdf_structure import extract_structure

async def run():
    with open('2209.11154v3.pdf', 'rb') as f:
        pdf_bytes = f.read()

    grobid_structure = await extract_via_grobid(pdf_bytes)
    heuristic_structure = extract_structure(pdf_bytes)
    
    print("Grobid abstract length:", len(grobid_structure.get("abstract", "")))
    print("Grobid abstract conf:", grobid_structure["confidence"]["abstract"])
    print("Heuristic abstract length:", len(heuristic_structure.get("abstract", "")))
    print("Heuristic abstract conf:", heuristic_structure["confidence"]["abstract"])
    
    merged = dict(grobid_structure)
    merged_conf = dict(grobid_structure.get("confidence", {}))
    for field in ("title", "authors", "abstract", "sections"):
        if grobid_structure.get("confidence", {}).get(field) == "low":
            heur_conf = heuristic_structure.get("confidence", {}).get(field, "low")
            if heur_conf == "high" and heuristic_structure.get(field):
                merged[field] = heuristic_structure[field]
                merged_conf[field] = "high"
    merged["confidence"] = merged_conf
    
    print("Merged abstract length:", len(merged["abstract"]))
    print("Merged sections keys:", list(merged["sections"].keys()))

asyncio.run(run())
