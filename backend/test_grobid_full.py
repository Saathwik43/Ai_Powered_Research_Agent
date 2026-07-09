import asyncio
from ai.grobid_client import _post_pdf
import httpx

async def run():
    with open('2209.11154v3.pdf', 'rb') as f:
        pdf_bytes = f.read()

    async with httpx.AsyncClient() as client:
        fulltext_xml = await _post_pdf(client, "/api/processFulltextDocument", pdf_bytes)
        with open('fulltext.xml', 'w', encoding='utf-8') as f:
            f.write(fulltext_xml if fulltext_xml else "")

asyncio.run(run())
