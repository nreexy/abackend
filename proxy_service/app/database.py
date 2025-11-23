import os
import json
import datetime
import uuid
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as redis
from bson.objectid import ObjectId
from pymongo import ASCENDING, DESCENDING
import httpx


# --- CONFIGURATION ---
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# --- DATABASE CLIENTS ---
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.audiobook_metadata

# Collections
books_collection = db.books          # Main Library Storage (Persistent)
custom_fields_collection = db.custom_fields
logs_collection = db.request_logs
settings_collection = db.settings
lists_collection = db.lists
provider_stats_collection = db.provider_stats

# Redis (Cache Layer)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
CACHE_TTL = 86400 # 24 Hours

# --- INITIALIZATION (SCALING) ---
async def init_db_indexes():
    """
    Creates indexes to ensure performance with 100k+ items.
    Called on app startup.
    """
    # Unique ASIN
    await books_collection.create_index([("asin", ASCENDING)], unique=True)
    # Search/Sort fields
    await books_collection.create_index([("title", ASCENDING)])
    await books_collection.create_index([("authors", ASCENDING)])
    await books_collection.create_index([("added_at", DESCENDING)])
    await books_collection.create_index([("last_accessed", DESCENDING)])

# --- CORE LIBRARY LOGIC (MONGODB) ---

async def upsert_book_to_db(book_data: dict):
    """
    Saves or Updates a book in MongoDB (Permanent Storage).
    Handles stats logic to prevent overwriting counts or creation dates.
    """
    if not book_data or "asin" not in book_data: return

    now = datetime.datetime.utcnow()
    
    # Prepare update data
    update_data = book_data.copy()
    
    # --- FIX: REMOVE CONFLICTING FIELDS ---
    # We do NOT want to overwrite these if the book already exists
    update_data.pop("added_at", None) 
    update_data.pop("access_count", None) # Fixes the Code 40 Conflict Error
    
    # Always update modification time
    update_data["updated_at"] = now

    await books_collection.update_one(
        {"asin": book_data["asin"]},
        {
            "$set": update_data,
            # Only set these if the document is being inserted (New Book)
            "$setOnInsert": {"added_at": now, "access_count": 1}
        },
        upsert=True
    )

async def get_book_from_db(asin: str):
    """Retrieves a book from MongoDB."""
    return await books_collection.find_one({"asin": asin}, {"_id": 0})

async def get_library_page(
    page: int = 1, 
    limit: int = 50, 
    sort_by: str = "added_at", 
    order: int = -1,
    filters: dict = None  # <--- NEW PARAMETER
):
    """
    Paginated fetch with filtering capabilities.
    """
    skip = (page - 1) * limit
    
    # 1. Build Query Object
    query = {}
    if filters:
        # Rating: Greater than or equal
        if filters.get("min_rating"):
            query["rating"] = {"$gte": float(filters["min_rating"])}
        
        # Provider: Exact match
        if filters.get("provider"):
            query["provider"] = filters["provider"]
            
        # Language: Exact match (case insensitive handled by normalization, but DB is case sensitive)
        if filters.get("language"):
            query["language"] = filters["language"].lower()
            
        # Year: String starts with YYYY (Regex)
        if filters.get("year"):
            query["published_date"] = {"$regex": f"^{filters['year']}"}

    # 2. Execute Query
    cursor = books_collection.find(query, {"_id": 0})
    cursor.sort(sort_by, order).skip(skip).limit(limit)
    books = await cursor.to_list(length=limit)
    
    # 3. Get Count (matching the filter)
    total_count = await books_collection.count_documents(query)
    
    # Format Dates/Lists for Display (Keep existing formatting logic)
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
    """Removes from both Permanent DB and Cache."""
    await books_collection.delete_one({"asin": asin})
    await redis_client.delete(f"book_v7:{asin}")

# --- CACHE FUNCTIONS (REDIS) ---

async def get_cache(key: str):
    data = await redis_client.get(key)
    return json.loads(data) if data else None

async def set_cache(key: str, data: dict, expire: int = CACHE_TTL):
    # Helper to serialize datetimes for JSON
    def json_serial(obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    await redis_client.set(key, json.dumps(data, default=json_serial), ex=expire)

async def inspect_cache(limit: int = 100):
    """For the Cache Inspector UI"""
    items = []
    count = 0
    async for key in redis_client.scan_iter("*"):
        if count >= limit: break
        val = await redis_client.get(key)
        ttl = await redis_client.ttl(key)
        
        item_type = "Unknown"
        preview = "N/A"
        size = len(val) if val else 0
        
        if "search" in key: item_type = "Search Query"
        elif "book" in key: item_type = "Book Data"

        try:
            if val:
                data = json.loads(val)
                preview = data.get("title", "Unknown Title")
                if preview == "Unknown Title" and "asin" in data:
                    preview = f"ASIN: {data['asin']}"
        except:
            preview = str(val)[:50]

        items.append({
            "key": key, "type": item_type, "preview": preview,
            "ttl": ttl, "size": f"{round(size/1024, 2)} KB"
        })
        count += 1
    return items

async def delete_cache_key(key: str):
    await redis_client.delete(key)

async def flush_all_cache():
    await redis_client.flushdb()

# --- SETTINGS LOGIC ---
DEFAULT_SETTINGS = {
    "providers": {"audible": True, "itunes": True, "goodreads": True, "prh": True},
    "search_limit": 5, "scrape_limit_pages": 100 
}

async def get_system_settings():
    config = await settings_collection.find_one({"_id": "global_config"})
    return config if config else DEFAULT_SETTINGS

async def save_system_settings(providers: dict, search_limit: int, scrape_limit_pages: int):
    """Upsert settings including scrape limit"""
    await settings_collection.update_one(
        {"_id": "global_config"},
        {"$set": {
            "providers": providers,
            "search_limit": search_limit,
            "scrape_limit_pages": scrape_limit_pages # <--- Save new field
        }},
        upsert=True
    )

# --- CUSTOM FIELDS ---
async def get_custom_fields(asin: str):
    return await custom_fields_collection.find_one({"asin": asin}, {"_id": 0})

async def save_custom_fields(asin: str, fields: dict):
    await custom_fields_collection.update_one({"asin": asin}, {"$set": fields}, upsert=True)

# --- LOGGING & STATS ---
async def log_activity(action: str, target: str, details: str = None):
    await logs_collection.insert_one({
        "timestamp": datetime.datetime.utcnow(),
        "action": action, "target": target, "details": details
    })

async def log_provider_stats(request_id: str, provider: str, duration_ms: float, result_count: int, status: str):
    await provider_stats_collection.insert_one({
        "timestamp": datetime.datetime.utcnow(),
        "request_id": request_id, "provider": provider,
        "duration_ms": duration_ms, "result_count": result_count, "status": status
    })

async def get_dashboard_stats():
    total_requests = await logs_collection.count_documents({})
    pipeline = [
        {"$match": {"action": "fetch_metadata"}},
        {"$group": {"_id": "$target", "title": {"$first": "$details"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 10}
    ]
    top_books = await logs_collection.aggregate(pipeline).to_list(length=10)
    recent_logs = await logs_collection.find().sort("timestamp", -1).limit(20).to_list(length=20)
    return {"total": total_requests, "top_books": top_books, "recent_logs": recent_logs}

async def get_detailed_stats():
    pipeline = [
        {
            "$group": {
                "_id": "$provider",
                "total_calls": {"$sum": 1},
                "total_results": {"$sum": "$result_count"},
                "avg_latency": {"$avg": "$duration_ms"},
                "successful_calls": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}}
            }
        },
        {"$sort": {"total_calls": -1}}
    ]
    stats = await provider_stats_collection.aggregate(pipeline).to_list(length=None)
    recent = await provider_stats_collection.find().sort("timestamp", -1).limit(50).to_list(length=50)
    return {"aggregated": stats, "recent": recent}

# --- LISTS LOGIC ---
async def save_imported_list(name: str, url: str, asins: list, source: str = "Audible"):
    """Saves a list of ASINs with a Source identifier"""
    doc = {
        "name": name, 
        "url": url, 
        "asins": asins, 
        "count": len(asins),
        "type": "imported",
        "source": source, # <--- Save the source field
        "created_at": datetime.datetime.utcnow(),
        "updated_at": datetime.datetime.utcnow()
    }
    await lists_collection.update_one({"url": url}, {"$set": doc}, upsert=True)

async def create_custom_list(name: str, asins: list):
    internal_id = f"custom:{uuid.uuid4()}"
    doc = {
        "name": name, "url": internal_id, "asins": asins, "count": len(asins),
        "type": "custom",
        "created_at": datetime.datetime.utcnow(),
        "updated_at": datetime.datetime.utcnow()
    }
    await lists_collection.insert_one(doc)
    return internal_id

async def get_all_lists():
    return await lists_collection.find().sort("created_at", -1).to_list(length=None)

async def get_list_by_id(list_id: str):
    try:
        return await lists_collection.find_one({"_id": ObjectId(list_id)})
    except: return None



    # --- UPDATE LOGGING ---
async def log_activity(
    action: str, 
    target: str, 
    details: str = None, 
    ip: str = "Unknown", 
    duration_ms: float = 0.0 # <--- NEW PARAMETER
):
    """
    Logs activity with IP address and Duration.
    """
    await logs_collection.insert_one({
        "timestamp": datetime.datetime.utcnow(),
        "action": action,
        "target": target,
        "details": details,
        "ip": ip,
        "duration_ms": duration_ms # <--- Save field
    })

# --- NEW STATISTICS FUNCTION ---
async def get_traffic_stats():
    """
    Calculates:
    1. Total Requests
    2. Distinct Devices (Unique IPs)
    3. Avg Requests per Device
    4. Recent Logs with IP
    """
    # 1. Total Count
    total_requests = await logs_collection.count_documents({})

    # 2. Distinct Devices (Unique IPs)
    # Note: .distinct() can be slow on massive datasets, but fine for <1M logs.
    # For massive scale, we would use the aggregation framework.
    unique_ips = await logs_collection.distinct("ip")
    distinct_devices = len(unique_ips)

    # 3. Average
    avg_per_device = 0
    if distinct_devices > 0:
        avg_per_device = round(total_requests / distinct_devices, 2)

    # 4. Recent Logs (Table Data)
    # We exclude 'fetch_error' from the table to keep it clean, or remove the filter to see all.
    cursor = logs_collection.find().sort("timestamp", -1).limit(100)
    recent_logs = await cursor.to_list(length=100)

    return {
        "total_requests": total_requests,
        "distinct_devices": distinct_devices,
        "avg_per_device": avg_per_device,
        "logs": recent_logs
    }


async def get_traffic_stats():
    """
    Calculates traffic stats including Country distribution.
    """
    # 1. Total Count
    total_requests = await logs_collection.count_documents({})

    # 2. Distinct Devices & IP Aggregation
    # Group by IP first to get counts per device
    pipeline = [
        {"$group": {"_id": "$ip", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    ip_groups = await logs_collection.aggregate(pipeline).to_list(length=None)
    distinct_devices = len(ip_groups)

    # 3. Country Resolution (With Redis Caching)
    country_stats = {} # {"US": 100, "DE": 50}
    
    for entry in ip_groups:
        ip = entry["_id"]
        count = entry["count"]
        
        if not ip or ip == "Unknown" or ip == "127.0.0.1":
            country = "Local/Unknown"
        else:
            # Check Cache First
            geo_key = f"geo:{ip}"
            cached_country = await redis_client.get(geo_key)
            
            if cached_country:
                country = cached_country
            else:
                # Fetch from API if not cached
                try:
                    # Using ip-api.com (Free for non-commercial, 45 req/min)
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(f"http://ip-api.com/json/{ip}", timeout=2.0)
                        if resp.status_code == 200:
                            data = resp.json()
                            country = data.get("countryCode", "Unknown")
                            # Cache for 30 days (IPs rarely change countries)
                            await redis_client.set(geo_key, country, ex=2592000)
                        else:
                            country = "Unknown"
                except:
                    country = "Unknown"

        # Aggregate into Country Map
        country_stats[country] = country_stats.get(country, 0) + count

    # Format Country Stats for UI (Sort by count)
    sorted_countries = [
        {"code": k, "count": v, "percent": round((v/total_requests)*100, 1)} 
        for k, v in country_stats.items()
    ]
    sorted_countries.sort(key=lambda x: x['count'], reverse=True)

    # 4. Averages
    avg_per_device = 0
    if distinct_devices > 0:
        avg_per_device = round(total_requests / distinct_devices, 2)

    # 5. Recent Logs
    cursor = logs_collection.find().sort("timestamp", -1).limit(100)
    recent_logs = await cursor.to_list(length=100)

    return {
        "total_requests": total_requests,
        "distinct_devices": distinct_devices,
        "avg_per_device": avg_per_device,
        "countries": sorted_countries, # <--- NEW DATA
        "logs": recent_logs
    }