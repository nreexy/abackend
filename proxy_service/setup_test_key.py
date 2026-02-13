import asyncio
import os
from app.database import settings_collection

async def set_key():
    print("Setting static key in DB...")
    await settings_collection.update_one(
        {"_id": "global_config"},
        {"$set": {"static_api_key": "my_super_secret_static_key"}},
        upsert=True
    )
    print("Key set.")
    
    # Verify
    config = await settings_collection.find_one({"_id": "global_config"})
    print(f"Current Config Static Key: {config.get('static_api_key')}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(set_key())
