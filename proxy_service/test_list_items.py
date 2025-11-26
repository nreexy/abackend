import asyncio
import sys
from unittest.mock import MagicMock, AsyncMock

# Mock dependencies
sys.modules["httpx"] = MagicMock()
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()
sys.modules["pymongo"] = MagicMock()
sys.modules["motor"] = MagicMock()
sys.modules["motor.motor_asyncio"] = MagicMock()
sys.modules["bson"] = MagicMock()
sys.modules["bson.objectid"] = MagicMock()
sys.modules["slowapi"] = MagicMock()
sys.modules["slowapi.errors"] = MagicMock()
sys.modules["fastapi"] = MagicMock()
sys.modules["audible"] = MagicMock()
sys.modules["feedparser"] = MagicMock()
sys.modules["lxml"] = MagicMock()
sys.modules["lxml.html"] = MagicMock()
sys.modules["fastapi.middleware.cors"] = MagicMock()
sys.modules["fastapi.middleware.trustedhost"] = MagicMock()
sys.modules["pydantic"] = MagicMock()
sys.modules["passlib"] = MagicMock()
sys.modules["passlib.context"] = MagicMock()
sys.modules["bs4"] = MagicMock()

# Mock Pydantic
class MockBaseModel:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
sys.modules["pydantic"].BaseModel = MockBaseModel

# Mock FastAPI
class MockAPIRouter:
    def get(self, *args, **kwargs):
        def decorator(func): return func
        return decorator
    def post(self, *args, **kwargs):
        def decorator(func): return func
        return decorator
sys.modules["fastapi"].APIRouter = MockAPIRouter
sys.modules["fastapi"].Query = lambda default, **kwargs: default

# Import after mocking
from app.routers.api import get_list_items, ListItemDefault, ListItemEnhanced

async def test_list_items():
    print("Testing GET /lists/{id}/items...")
    
    # Setup Mocks
    mock_request = MagicMock()
    
    # Mock Database Functions
    import app.routers.api
    
    # Mock List Object
    mock_list = {
        "_id": "list_123",
        "name": "Test List",
        "asins": ["A1", "A2", "A3", "A4", "A5"]
    }
    app.routers.api.get_list_by_id = AsyncMock(return_value=mock_list)
    
    # Mock Book Data
    async def mock_get_cache(key):
        if key == "book_v7:A1":
            return {"asin": "A1", "title": "Book One", "authors": ["Author One"], "genres": ["SciFi"], "rating": 5.0}
        return None
        
    async def mock_get_db(asin):
        if asin == "A2":
            return {"asin": "A2", "title": "Book Two", "authors": ["Author Two"], "genres": ["Fantasy"], "rating": 4.0}
        return None
        
    app.routers.api.get_cache = mock_get_cache
    app.routers.api.get_book_from_db = mock_get_db
    app.routers.api.set_cache = AsyncMock()
    
    # Test 1: Default Level, Page 1, Limit 2
    print("Test 1: Default Level, Pagination")
    response = await get_list_items("list_123", mock_request, page=1, limit=2, enhanced=False)
    
    assert response.total_count == 5
    assert response.page == 1
    assert response.total_pages == 3 # 5 items / 2 per page = 3 pages
    assert len(response.items) == 2
    assert response.items[0].asin == "A1"
    assert response.items[0].title == "Book One"
    # Ensure enhanced fields are NOT present (or ignored in this mock model check)
    # Since we use MockBaseModel, attributes are set dynamically. 
    # But strictly speaking, the response model should filter. 
    # In this unit test with MockBaseModel, we just check what we got.
    
    # Test 2: Enhanced Level
    print("Test 2: Enhanced Level")
    response = await get_list_items("list_123", mock_request, page=1, limit=5, enhanced=True)
    
    item1 = response.items[0]
    assert item1.genres == ["SciFi"]
    assert item1.rating == 5.0
    
    item2 = response.items[1]
    assert item2.genres == ["Fantasy"]
    assert item2.rating == 4.0
    
    item3 = response.items[2] # A3 (Unknown)
    assert item3.title == "Unknown Title"
    
    print("✅ All Tests Passed!")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_list_items())
