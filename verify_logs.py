import asyncio
import os
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

# Config (running inside container)
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo_db:27017")
BASE_URL = "http://localhost:8000"

async def verify():
    print("1. Generating Traffic...")
    async with httpx.AsyncClient() as client:
        # Valid Request (Health Check - filtered out? No, /ping /metrics /favicon are filtered. / is filtered?)
        # Middleware filters: ["/ping", "/metrics", "/favicon.ico"]
        # So "/" should be logged.
        try:
            r = await client.get(f"{BASE_URL}/", timeout=5)
            print(f"   GET / -> {r.status_code}")
        except Exception as e:
            print(f"   GET / failed: {e}")

        # Invalid Request (404)
        try:
            r = await client.get(f"{BASE_URL}/non-existent-page-123", timeout=5)
            print(f"   GET /non-existent-page-123 -> {r.status_code}")
        except Exception as e:
             print(f"   GET /non-existent failed: {e}")

    print("\n2. Checking MongoDB Logs...")
    client = AsyncIOMotorClient(MONGO_URL)
    db = client.audiobook_metadata
    collection = db.access_logs
    
    # Allow a moment for async write
    await asyncio.sleep(1)

    count = await collection.count_documents({})
    print(f"   Total Access Logs: {count}")
    
    # Fetch recent
    logs = await collection.find().sort("timestamp", -1).limit(5).to_list(length=5)
    for log in logs:
        print(f"   - [{log['timestamp']}] {log['method']} {log['path']} ({log['status_code']}) IP={log['ip']}")

    if count > 0:
        print("\nSUCCESS: Logs are being recorded.")
    else:
        print("\nFAILURE: No logs found.")

if __name__ == "__main__":
    asyncio.run(verify())
