import asyncio
import os
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

# Config
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo_db:27017")
BASE_URL = "http://localhost:8000"
TEST_KEY = "test-static-key-123"

async def verify():
    print("1. Setting up Test Auth (Static Key)...")
    client = AsyncIOMotorClient(MONGO_URL)
    db = client.audiobook_metadata
    
    # Enable Static Key
    await db.settings.update_one(
        {"_id": "global_config"},
        {"$set": {"static_api_key": TEST_KEY}},
        upsert=True
    )
    print(f"   Key set to: {TEST_KEY}")

    count_before = await db.access_logs.count_documents({})
    print(f"   Logs before traffic: {count_before}")

    print("\n1.5. Generating Traffic...")
    async with httpx.AsyncClient() as client:
        await client.get(f"{BASE_URL}/")
        await client.get(f"{BASE_URL}/non-existent-page")
        await client.get(f"{BASE_URL}/")
    
    count_after = await db.access_logs.count_documents({})
    print(f"   Logs after traffic: {count_after}")

    print("\n2. Testing CSV Download...")
    async with httpx.AsyncClient() as http_client:
        headers = {"Authorization": f"Bearer {TEST_KEY}"}
        
        try:
            # We use stream=True to verify it's a streaming response, though for small data it comes at once
            async with http_client.stream("GET", f"{BASE_URL}/settings/logs/download", headers=headers, timeout=10) as response:
                print(f"   Status Code: {response.status_code}")
                
                if response.status_code != 200:
                    print(f"   FAILURE: Expected 200, got {response.status_code}")
                    print(f"   Body: {await response.read()}")
                    return

                # Check Headers
                disp = response.headers.get("content-disposition", "")
                print(f"   Content-Disposition: {disp}")
                if "attachment" not in disp or "access_logs.csv" not in disp:
                     print("   WARNING: Content-Disposition header looks wrong.")

                # Read Content
                content = ""
                async for chunk in response.aiter_text():
                    content += chunk
                
                lines = content.strip().split("\n")
                print(f"   Total Lines: {len(lines)}")
                if len(lines) > 0:
                    print(f"   Header: {lines[0]}")
                if len(lines) > 1:
                    print(f"   First Row: {lines[1]}")
                
                if "Timestamp,Status,Method" in lines[0]:
                    print("\nSUCCESS: CSV Download verified.")
                else:
                    print("\nFAILURE: CSV Header missing.")

        except Exception as e:
            print(f"   Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(verify())
