import asyncio
import os
import sys
import httpx

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.database import get_system_settings

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"

async def verify_alternatives():
    print("🕵️‍♂️ Testing Hardcover Alternatives...")
    
    config = await get_system_settings()
    clean_key = config.get("hardcover_api_key", "").replace("Bearer", "").strip()
    
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json"
    }
    
    # Test 5: Title Exact Match
    print("\n[Test 5] Title _eq match...")
    q5 = """
    query {
      books(where: {title: {_eq: "project hail mary"}}, limit: 1) {
        title
      }
    }
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(HARDCOVER_API_URL, json={"query": q5}, headers=headers)
        if resp.status_code == 200 and "errors" not in resp.json():
            print(f"✅ FOUND title eq! \n{resp.json()}")
        else:
            print(f"❌ Failed: {resp.status_code} {resp.text}")

if __name__ == "__main__":
    import app.database
    asyncio.run(verify_alternatives())
