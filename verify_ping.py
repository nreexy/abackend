import asyncio
import os
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

# Config
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo_db:27017")
BASE_URL = "http://localhost:8000"

async def verify():
    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(MONGO_URL)
    db = client.audiobook_metadata
    
    # 1. Check Initial Count
    count_start = await db.access_logs.count_documents({"path": "/ping"})
    print(f"Initial Ping Count: {count_start}")

    # 2. Send Ping Request
    print("Sending /ping request...")
    async with httpx.AsyncClient() as http_client:
        try:
            resp = await http_client.get(f"{BASE_URL}/ping")
            print(f"Ping Status: {resp.status_code}")
        except Exception as e:
            print(f"Request failed: {e}")
            return

    # 3. Check Final Count
    count_end = await db.access_logs.count_documents({"path": "/ping"})
    print(f"Final Ping Count: {count_end}")

    if count_end > count_start:
        print("SUCCESS: Ping request logged and counted.")
    else:
        print("FAILURE: Ping request NOT count incremented.")

if __name__ == "__main__":
    asyncio.run(verify())
