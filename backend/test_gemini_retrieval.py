import asyncio
from dotenv import load_dotenv
load_dotenv()
from ai.manuscript_generation import generate_section
import json

async def main():
    topic = "Quantum Error Correction"
    section = "lit_review"
    context = ""

    print(f"Testing generate_section for topic: '{topic}', section: '{section}'")
    content, flags = await generate_section(topic, section, context)
    
    print("\n--- Output Content ---")
    print(content)
    
    print("\n--- Flags/References ---")
    print(json.dumps(flags, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
