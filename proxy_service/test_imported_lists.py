import sys
from unittest.mock import MagicMock

# Mock dependencies that might be missing or require complex setup
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

# We need to define BaseModel so it can be inherited
class MockBaseModel:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

sys.modules["pydantic"].BaseModel = MockBaseModel

# We need to define APIRouter so it can be instantiated
class MockAPIRouter:
    def get(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator
    def post(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

sys.modules["fastapi"].APIRouter = MockAPIRouter

from app.routers.api import get_imported_lists, ImportedListResponse

# Mock Request
mock_request = MagicMock()

# Mock Data
mock_lists = [
    {
        "_id": "507f1f77bcf86cd799439011",
        "name": "Best Sci-Fi",
        "type": "imported",
        "source": "Audible",
        "count": 10,
        "created_at": datetime.datetime(2023, 1, 1, 12, 0, 0)
    },
    {
        "_id": "507f1f77bcf86cd799439012",
        "name": "My Favorites",
        "type": "custom",
        "source": "Custom",
        "count": 5,
        "created_at": datetime.datetime(2023, 1, 2, 12, 0, 0)
    },
    {
        "_id": "507f1f77bcf86cd799439013",
        "name": "Top Thrillers",
        "type": "imported",
        "source": "Goodreads",
        "count": 20,
        "created_at": "2023-01-03" # String date case
    }
]

async def test_endpoint():
    print("Testing GET /lists/imported...")
    
    with patch("app.routers.api.get_all_lists", new_callable=MagicMock) as mock_get_all:
        mock_get_all.return_value = mock_lists
        
        # Call the endpoint function directly
        response = await get_imported_lists(mock_request)
        
        # Verify
        assert len(response) == 2, f"Expected 2 lists, got {len(response)}"
        
        # Check first item
        item1 = response[0]
        assert item1.name == "Best Sci-Fi"
        assert item1.source == "Audible"
        assert item1.imported_at == "2023-01-01"
        
        # Check second item
        item2 = response[1]
        assert item2.name == "Top Thrillers"
        assert item2.source == "Goodreads"
        assert item2.imported_at == "2023-01-03"
        
        print("✅ Test Passed!")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_endpoint())
