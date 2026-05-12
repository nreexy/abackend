import datetime
import uuid
from pymongo import ASCENDING, DESCENDING
from .core import books_collection, unified_catalog_collection, custom_fields_collection, redis_client

# --- INITIALIZATION ---
async def init_db_indexes():
    """Creates indexes for performance on startup."""
    await books_collection.create_index([("asin", ASCENDING)], unique=True)
    await books_collection.create_index([("title", ASCENDING)])
    await books_collection.create_index([("authors", ASCENDING)])
    await books_collection.create_index([("added_at", DESCENDING)])
    await books_collection.create_index([("last_accessed", DESCENDING)])

# --- CORE LIBRARY LOGIC (MONGODB) ---

async def upsert_book_to_db(book_data: dict):
    """
    Saves book to MongoDB. Removes conflicting fields to prevent Code 40 errors.
    """
    if not book_data or "asin" not in book_data: return

    now = datetime.datetime.utcnow()
    
    # Prepare update data
    update_data = book_data.copy()
    # Remove fields that should NOT be overwritten on update
    update_data.pop("added_at", None) 
    update_data.pop("access_count", None) # Fixes MongoDB Conflict Error
    
    update_data["updated_at"] = now

    await books_collection.update_one(
        {"asin": book_data["asin"]},
        {
            "$set": update_data,
            "$setOnInsert": {"added_at": now, "access_count": 1}
        },
        upsert=True
    )

async def get_book_from_db(asin: str):
    return await books_collection.find_one({"asin": asin}, {"_id": 0})

async def get_books_from_db_batch(asins: list) -> dict:
    """Fetch multiple books in one MongoDB query. Returns {asin: doc}."""
    if not asins:
        return {}
    cursor = books_collection.find({"asin": {"$in": asins}}, {"_id": 0})
    return {doc["asin"]: doc for doc in await cursor.to_list(length=len(asins))}

async def get_library_page(page: int = 1, limit: int = 50, sort_by: str = "added_at", order: int = -1, filters: dict = None):
    """
    Paginated fetch with filtering.
    """
    skip = (page - 1) * limit
    
    query = {}
    if filters:
        if filters.get("min_rating"):
            query["rating"] = {"$gte": float(filters["min_rating"])}
        if filters.get("provider"):
            query["provider"] = filters["provider"]
        if filters.get("language"):
            query["language"] = filters["language"].lower()
        if filters.get("year"):
            query["published_date"] = {"$regex": f"^{filters['year']}"}

    cursor = books_collection.find(query, {"_id": 0})
    cursor.sort(sort_by, order).skip(skip).limit(limit)
    books = await cursor.to_list(length=limit)
    total_count = await books_collection.count_documents(query)
    
    # Format for UI
    formatted = []
    for data in books:
        data['authors_str'] = ", ".join(data.get("authors", []))
        data['narrators_str'] = ", ".join(data.get("narrators", []))
        data['genres_str'] = ", ".join(data.get("genres", []))
        s = data.get("series", [])
        data['series_str'] = f"{s[0].get('name')} #{s[0].get('sequence')}" if s else "-"
        
        added = data.get("added_at")
        if isinstance(added, datetime.datetime): data['cached_at'] = added.strftime("%Y-%m-%d")
        else: data['cached_at'] = str(added)[:10]

        accessed = data.get("last_accessed")
        if isinstance(accessed, datetime.datetime): data['last_accessed'] = accessed.strftime("%Y-%m-%d %H:%M")
        
        formatted.append(data)
        
    return formatted, total_count

async def delete_book_from_library(asin: str):
    await books_collection.delete_one({"asin": asin})
    await redis_client.delete(f"book_v7:{asin}")

async def increment_book_access(asin: str):
    """
    Increments access_count in MongoDB and patches the Redis cache to match.
    """
    import json
    from .core import CACHE_TTL
    now = datetime.datetime.utcnow()

    # 1. Increment in MongoDB and get the updated document
    updated = await books_collection.find_one_and_update(
        {"asin": asin},
        {
            "$inc": {"access_count": 1},
            "$set": {"last_accessed": now}
        },
        return_document=True,
        projection={"access_count": 1, "_id": 0}
    )
    if not updated:
        return

    new_count = updated.get("access_count", 1)
    now_str = now.isoformat()

    # 2. Patch the Redis cache in-place so reads immediately see the new values
    cache_key = f"book_v7:{asin}"
    raw = await redis_client.get(cache_key)
    if raw:
        try:
            cached = json.loads(raw)
            cached["access_count"] = new_count
            cached["last_accessed"] = now_str
            ttl = await redis_client.ttl(cache_key)
            expire = ttl if ttl and ttl > 0 else CACHE_TTL
            await redis_client.set(cache_key, json.dumps(cached), ex=expire)
        except Exception:
            pass

async def search_library_books(query: str, limit: int = 10):
    """
    Search for books in the local library by title or author.
    """
    if not query: return []
    
    # Case-insensitive regex search
    regex = {"$regex": query, "$options": "i"}
    
    search_filter = {
        "$or": [
            {"title": regex},
            {"authors": regex},
            {"asin": regex}
        ]
    }
    
    cursor = books_collection.find(search_filter, {"_id": 0})
    cursor.limit(limit)
    
    return await cursor.to_list(length=limit)

# --- CUSTOM FIELDS ---
async def get_custom_fields(asin: str):
    return await custom_fields_collection.find_one({"asin": asin}, {"_id": 0})

async def save_custom_fields(asin: str, fields: dict):
    await custom_fields_collection.update_one({"asin": asin}, {"$set": fields}, upsert=True)

# --- UNIFIED CATALOG ---

async def get_unified_book(unified_id: str):
    return await unified_catalog_collection.find_one({"_id": unified_id})

async def find_unified_by_relation(provider: str, provider_id: str):
    """
    Finds a Unified Book that contains a specific provider ID relation.
    """
    return await unified_catalog_collection.find_one({
        "relations": {"$elemMatch": {"provider": provider, "id": provider_id}}
    })

async def create_unified_book(title: str, authors: list, relations: list):
    """
    Creates a new Unified Book entry.
    """
    uid = str(uuid.uuid4())
    now = datetime.datetime.utcnow()
    doc = {
        "_id": uid,
        "title": title,
        "authors": authors,
        "relations": relations, # List of {provider, id}
        "created_at": now,
        "updated_at": now
    }
    await unified_catalog_collection.insert_one(doc)
    return doc

async def add_relation_to_unified_book(unified_id: str, provider: str, provider_id: str):
    """
    Adds a new provider link to an existing Unified Book.
    """
    await unified_catalog_collection.update_one(
        {"_id": unified_id},
        {
            "$push": {"relations": {"provider": provider, "id": provider_id}},
            "$set": {"updated_at": datetime.datetime.utcnow()}
        }
    )
