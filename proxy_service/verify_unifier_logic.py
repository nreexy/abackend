import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services import unifier

# Mock Data
audible_book = {
    "provider": "Audible", "asin": "B08GBH8", 
    "title": "Project Hail Mary", "authors": ["Andy Weir"], "isbn": None
}
hardcover_book = {
    "provider": "Hardcover", "asin": "hc:project-hail-mary", 
    "title": "Project Hail Mary", "authors": ["Andy Weir"], "isbn": None
}
google_book = {
    "provider": "Google Books", "asin": "gb:12345", 
    "title": "Project Hail Mary", "authors": ["Andy Weir"], "isbn": "9780593135204"
}
random_book = {
    "provider": "Audible", "asin": "B09ABC", 
    "title": "The Martian", "authors": ["Andy Weir"], "isbn": None
}

async def test_unifier():
    print("🧪 Testing Unifier Logic...")
    
    # CASE 1: Perfect Title/Author Match
    results = [audible_book, hardcover_book]
    unified = await unifier.unify_search_results([results])
    
    print(f"\n[Case 1] Expected 1 unified book, Got {len(unified)}")
    if len(unified) == 1:
        u = unified[0]
        print(f"   ✅ Merged: {u['title']}")
        print(f"   Providers: {u['available_providers']}")
        if "Audible" in u['available_providers'] and "Hardcover" in u['available_providers']:
            print("   ✅ Providers list correct")
    else:
        print("   ❌ Failed to merge")

    # CASE 2: No Match
    results = [audible_book, random_book]
    unified = await unifier.unify_search_results([results])
    print(f"\n[Case 2] Expected 2 books, Got {len(unified)}")
    if len(unified) == 2:
        print("   ✅ Correctly kept separate")
    else:
        print("   ❌ Incorrectly merged")

if __name__ == "__main__":
    # We need to mock database calls or rely on them returning None (unassigned)
    # The current unifier implementation queries DB. 
    # For this unit test, it runs against the real DB in the container.
    import app.database
    asyncio.run(test_unifier())
