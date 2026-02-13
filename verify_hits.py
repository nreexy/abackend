import asyncio
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

# Config
MONGO_URL = "mongodb://mongo_db:27017"
API_URL = "http://localhost:8000"
TEST_ASIN = "B01N5T5M98" # Use a known ASIN or insert one

async def verify_hits():
    # 1. Connect to DB
    client = AsyncIOMotorClient(MONGO_URL)
    db = client.audiobook_metadata
    collection = db.books
    
    # 2. Get Initial Count
    book = await collection.find_one({"asin": TEST_ASIN})
    if not book:
        print(f"Creating test book {TEST_ASIN}...")
        await collection.insert_one({"asin": TEST_ASIN, "title": "Test Book", "access_count": 1})
        initial_count = 1
    else:
        initial_count = book.get("access_count", 0)
    
    print(f"Initial Count: {initial_count}")

    # 3. Trigger API Call (Needs Auth)
    # Check if we need a token or static key. 
    # Let's assume we can use the static key we set up earlier? 
    # Or just login. For simplicity, let's use the static key if available, 
    # or just bypass if we enabled that.
    # Actually, let's just use the static API key we set: "d41d8cd98f00b204e9800998ecf8427e" (from previous context if any)
    # Wait, I saw the user set "manual_key_123" in previous steps? 
    # Let's try to fetch a token first.
    
    # For this test, I'll just use a static key if I know it, 
    # Otherwise I'll try to login with default admin/admin (if not changed)
    # But wait, I can just read the static key from the DB to be sure.
    
    settings = await db.settings.find_one({"_id": "global_config"})
    static_key = settings.get("static_api_key")
    headers = {}
    if static_key:
        headers["Authorization"] = f"Bearer {static_key}"
        print(f"Using Static Key: {static_key}")
    else:
        print("No static key found. Verification might fail if auth is required.")

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_URL}/book/{TEST_ASIN}", headers=headers)
        if resp.status_code != 200:
            print(f"API Call Failed: {resp.status_code} - {resp.text}")
            return

    # 4. Wait for Background Task
    print("Waiting for background task...")
    await asyncio.sleep(2)

    # 5. Check Final Count
    book = await collection.find_one({"asin": TEST_ASIN})
    final_count = book.get("access_count", 0)
    print(f"Final Count: {final_count}")

    if final_count > initial_count:
        print("✅ SUCCESS: Hit count incremented!")
    else:
        print("❌ FAILURE: Hit count did not increment.")

if __name__ == "__main__":
    asyncio.run(verify_hits())
