import asyncio
from ai.grobid_client import _post_pdf
import httpx

async def run():
    with open('2209.11154v3.pdf', 'rb') as f:
        pdf_bytes = f.read()

    async with httpx.AsyncClient() as client:
        header_xml = await _post_pdf(client, "/api/processHeaderDocument", pdf_bytes)
        with open('header.xml', 'w') as f:
            f.write(header_xml if header_xml else "")

asyncio.run(run())
