import asyncio
from dotenv import load_dotenv
load_dotenv()

from ai.topic_discovery import discover_topics
from ai.manuscript_generation import generate_section

async def main():
    print("--- Topic Discovery ---")
    try:
        t = await discover_topics("hrthwrtajarj")
        print("Topic Discovery Result:", t)
    except Exception as e:
        print("Error:", e)

    print("\n--- Manuscript Generation ---")
    try:
        m, f = await generate_section("hrthwrtajarj", "abstract", "")
        print("Manuscript Result:", repr(m))
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
