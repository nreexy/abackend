import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient

# Config
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo_db:27017")

async def verify():
    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(MONGO_URL)
    db = client.audiobook_metadata
    
    # 1. Fetch ALL logs to see what we have
    total = await db.access_logs.count_documents({})
    print(f"Total Logs: {total}")
    
    # 2. Test Filter: 200 OK
    print("\nTesting Filter: 200 OK...")
    logs_200 = await db.access_logs.find({"status_code": 200}).to_list(length=100)
    print(f"Found {len(logs_200)} logs with status 200.")
    for log in logs_200[:3]:
        print(f"   [OK] {log.get('status_code')} {log.get('path')}")
        if log.get('status_code') != 200:
            print("   [FAIL] Expected 200!")
            
    # 3. Test Filter: 404 Not Found
    print("\nTesting Filter: 404 Not Found...")
    logs_404 = await db.access_logs.find({"status_code": 404}).to_list(length=100)
    print(f"Found {len(logs_404)} logs with status 404.")
    for log in logs_404[:3]:
        print(f"   [OK] {log.get('status_code')} {log.get('path')}")
        if log.get('status_code') != 404:
            print("   [FAIL] Expected 404!")

    # 4. Test Filter: 401 Unauthorized (Ping)
    print("\nTesting Filter: 401 Unauthorized...")
    logs_401 = await db.access_logs.find({"status_code": 401}).to_list(length=100)
    print(f"Found {len(logs_401)} logs with status 401.")
    for log in logs_401[:3]:
        print(f"   [OK] {log.get('status_code')} {log.get('path')}")

if __name__ == "__main__":
    asyncio.run(verify())
