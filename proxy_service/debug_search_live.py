import asyncio
import os
import sys

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import get_system_settings
from app.services import hardcover

async def debug_hardcover():
    print("🕵️‍♂️ Debugging Hardcover Live Search...")
    
    # 1. Get Settings
    config = await get_system_settings()
    api_key = config.get("hardcover_api_key")
    
    if not api_key:
        print("❌ No Hardcover API Key found in settings.")
        return

    print(f"🔑 Found API Key: {api_key[:5]}...{api_key[-5:] if len(api_key)>10 else ''}")
    
    # 2. Run Search
    queries = ["Project Hail Mary", "project hail mary", "PROJECT HAIL MARY"]
    
    for q in queries:
        print(f"\n📡 Searching for: '{q}'")
        results = await hardcover.search_book(q, api_key)
        
        if results:
            print(f"✅ Success! Found {len(results)} books.")
            for b in results:
                print(f"   - {b['title']} (Auth: {b['authors']})")
        else:
            print("❌ No results returned.")

if __name__ == "__main__":
    # Ensure we can run it
    import app.database
    # Patch mongo client to use container internal host if needed?
    # The script acts as if it's inside the app structure.
    # If run via docker exec, it should have access to env vars and networks.
    
    asyncio.run(debug_hardcover())
