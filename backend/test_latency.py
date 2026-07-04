import asyncio
import time
from integrations.paper_search import search_all

async def main():
    print("Testing ML...")
    t0 = time.time()
    res1 = await search_all("machine learning", 15)
    t1 = time.time()
    print(f"ML: {len(res1)} results in {t1-t0:.2f}s")
    
    print("\nTesting AI...")
    t2 = time.time()
    res2 = await search_all("artificial intelligence", 15)
    t3 = time.time()
    print(f"AI: {len(res2)} results in {t3-t2:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())
