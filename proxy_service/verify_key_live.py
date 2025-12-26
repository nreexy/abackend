import asyncio
import os
import sys
import httpx

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.database import get_system_settings

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"

async def verify_key():
    print("🔑 Verifying Hardcover API Key (No Filters)...")
    
    config = await get_system_settings()
    api_key = config.get("hardcover_api_key")
    
    if not api_key:
        print("❌ No Key Found")
        return

    clean_key = api_key.replace("Bearer", "").strip()
    
    query = """
    query {
      books(limit: 1) {
        title
      }
    }
    """
    
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            HARDCOVER_API_URL, 
            json={"query": query}, 
            headers=headers
        )
        
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")

if __name__ == "__main__":
    import app.database
    asyncio.run(verify_key())
