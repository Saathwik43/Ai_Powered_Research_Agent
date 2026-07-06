import os
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("GEMINI_API_KEY is missing from .env")
    exit(1)

client = genai.Client(api_key=api_key)

async def test_gemini():
    try:
        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=50,
        )
        print("Sending test request to Gemini-2.5-pro...")
        response = await client.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents="Say 'Hello World' in a short sentence.",
            config=config,
        )
        print(f"Success! Response: {response.text.strip()}")
    except APIError as e:
        print(f"Gemini APIError ({e.code}): {e.message}")
        print(f"Full details: {e}")
    except Exception as e:
        print(f"Other Error ({type(e).__name__}): {e}")

if __name__ == "__main__":
    asyncio.run(test_gemini())
