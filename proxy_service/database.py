import os
import json
import datetime
import uuid
import hashlib # <--- Required for debounce hash
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
books_collection = db.books
custom_fields_collection = db.custom_fields
logs_collection = db.request_logs
settings_collection = db.settings
lists_collection = db.lists
provider_stats_collection = db.provider_stats

# Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
CACHE_TTL = 86400

# --- INITIALIZATION ---
async def init_db_indexes():
    await books_collection.create_index([("asin", ASCENDING)], unique=True)
    await books_collection.create_index([("title", ASCENDING)])
    await books_collection.create_index([("authors", ASCENDING)])
    await books_collection.create_index([("added_at", DESCENDING)])
    await books_collection.create_index([("last_accessed", DESCENDING)])

# --- CORE LIBRARY LOGIC ---
async def upsert_book_to_db(book_data: dict):
    if not book_data or "asin" not in book_data: return
    now = datetime.datetime.utcnow()
    update_data = book_data.copy()
    update_data.pop("added_at", None) 
    update_data.pop("access_count", None)
    update_data["updated_at"] = now

    await books_collection.update_one(
        {"asin": book_data["asin"]},
        {"$set": update_data, "$setOnInsert": {"added_at": now, "access_count": 1}},
        upsert=True
    )

async def get_book_from_db(asin: str):
    return await books_collection.find_one({"asin": asin}, {"_id": 0})

async def get_library_page(page: int = 1, limit: int = 50, sort_by: str = "added_at", order: int = -1):
    skip = (page - 1) * limit
    cursor = books_collection.find({}, {"_id": 0})
    cursor.sort(sort_by, order).skip(skip).limit(limit)
    books = await cursor.to_list(length=limit)
    total_count = await books_collection.count_documents({})
    
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

# --- CACHE FUNCTIONS ---
async def get_cache(key: str):
    data = await redis_client.get(key)
    return json.loads(data) if data else None

async def set_cache(key: str, data: dict, expire: int = CACHE_TTL):
    def json_serial(obj):
        if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    await redis_client.set(key, json.dumps(data, default=json_serial), ex=expire)

async def inspect_cache(limit: int = 100):
    items = []
    count = 0
    async for key in redis_client.scan_iter("*"):
        if count >= limit: break
        val = await redis_client.get(key)
        ttl = await redis_client.ttl(key)
        size = len(val) if val else 0
        item_type = "Search Query" if "search" in key else "Book Data" if "book" in key else "Unknown"
        try:
            data = json.loads(val)
            preview = data.get("title", f"ASIN: {data.get('asin', 'Unknown')}")
        except: preview = str(val)[:50]
        items.append({"key": key, "type": item_type, "preview": preview, "ttl": ttl, "size": f"{round(size/1024, 2)} KB"})
        count += 1
    return items

async def delete_cache_key(key: str):
    await redis_client.delete(key)

async def flush_all_cache():
    await redis_client.flushdb()

# --- LOGGING (ATOMIC LOCKING FIX) ---
async def log_activity(action: str, target: str, details: str = None, ip: str = "Unknown", duration_ms: float = 0.0):
    """
    Logs activity with Atomic Debouncing.
    """
    # 1. Generate Hash
    raw_key = f"{ip}:{action}:{target}"
    log_hash = hashlib.md5(raw_key.encode()).hexdigest()
    debounce_key = f"log_debounce:{log_hash}"

    # 2. Check Redis Lock
    # nx=True means "Only set if Not Exists"
    is_new = await redis_client.set(debounce_key, "1", ex=5, nx=True)

    if not is_new:
        # DEBUG PRINT: Check your docker logs for this!
        print(f"🛡️ DEBOUNCED: {action} - {target[:20]}...") 
        return

    # 3. Log to Mongo
    await logs_collection.insert_one({
        "timestamp": datetime.datetime.utcnow(),
        "action": action,
        "target": target,
        "details": details,
        "ip": ip,
        "duration_ms": duration_ms
    })

async def log_provider_stats(request_id: str, provider: str, duration_ms: float, result_count: int, status: str):
    await provider_stats_collection.insert_one({
        "timestamp": datetime.datetime.utcnow(),
        "request_id": request_id, "provider": provider,
        "duration_ms": duration_ms, "result_count": result_count, "status": status
    })

# --- STATS AGGREGATION ---
async def get_traffic_stats():
    total_requests = await logs_collection.count_documents({})
    
    pipeline = [{"$group": {"_id": "$ip", "count": {"$sum": 1}}}, {"$sort": {"count": -1}}]
    ip_groups = await logs_collection.aggregate(pipeline).to_list(length=None)
    distinct_devices = len(ip_groups)

    country_stats = {}
    for entry in ip_groups:
        ip = entry["_id"]
        count = entry["count"]
        if not ip or ip == "Unknown" or ip == "127.0.0.1":
            country = "Local"
        else:
            geo_key = f"geo:{ip}"
            cached = await redis_client.get(geo_key)
            if cached: country = cached
            else:
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(f"http://ip-api.com/json/{ip}", timeout=1.0)
                        country = resp.json().get("countryCode", "Unknown") if resp.status_code == 200 else "Unknown"
                        await redis_client.set(geo_key, country, ex=2592000)
                except: country = "Unknown"
        country_stats[country] = country_stats.get(country, 0) + count

    sorted_countries = [{"code": k, "count": v, "percent": round((v/total_requests)*100, 1)} for k, v in country_stats.items()]
    sorted_countries.sort(key=lambda x: x['count'], reverse=True)

    avg_per_device = round(total_requests / distinct_devices, 2) if distinct_devices else 0
    recent_logs = await logs_collection.find().sort("timestamp", -1).limit(100).to_list(length=100)

    return {"total_requests": total_requests, "distinct_devices": distinct_devices, "avg_per_device": avg_per_device, "countries": sorted_countries, "logs": recent_logs}

async def get_detailed_stats():
    pipeline = [
        {"$group": {"_id": "$provider", "total_calls": {"$sum": 1}, "total_results": {"$sum": "$result_count"}, "avg_latency": {"$avg": "$duration_ms"}, "successful_calls": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}}}},
        {"$sort": {"total_calls": -1}}
    ]
    stats = await provider_stats_collection.aggregate(pipeline).to_list(length=None)
    recent = await provider_stats_collection.find().sort("timestamp", -1).limit(50).to_list(length=50)
    return {"aggregated": stats, "recent": recent}

async def get_dashboard_stats():
    total_requests = await logs_collection.count_documents({})
    pipeline = [{"$match": {"action": "fetch_metadata"}}, {"$group": {"_id": "$target", "title": {"$first": "$details"}, "count": {"$sum": 1}}}, {"$sort": {"count": -1}}, {"$limit": 10}]
    top_books = await logs_collection.aggregate(pipeline).to_list(length=10)
    recent_logs = await logs_collection.find().sort("timestamp", -1).limit(20).to_list(length=20)
    return {"total": total_requests, "top_books": top_books, "recent_logs": recent_logs}

# --- SETTINGS & LISTS ---
DEFAULT_SETTINGS = {"providers": {"audible": True, "itunes": True, "goodreads": True, "prh": True}, "search_limit": 5}

async def get_system_settings():
    config = await settings_collection.find_one({"_id": "global_config"})
    return config if config else DEFAULT_SETTINGS

async def save_system_settings(providers: dict, limit: int):
    await settings_collection.update_one({"_id": "global_config"}, {"$set": {"providers": providers, "search_limit": limit}}, upsert=True)

async def save_imported_list(name: str, url: str, asins: list):
    doc = {"name": name, "url": url, "asins": asins, "count": len(asins), "type": "imported", "created_at": datetime.datetime.utcnow()}
    await lists_collection.update_one({"url": url}, {"$set": doc}, upsert=True)

async def create_custom_list(name: str, asins: list):
    internal_id = f"custom:{uuid.uuid4()}"
    doc = {"name": name, "url": internal_id, "asins": asins, "count": len(asins), "type": "custom", "created_at": datetime.datetime.utcnow()}
    await lists_collection.insert_one(doc)
    return internal_id

async def get_all_lists():
    return await lists_collection.find().sort("created_at", -1).to_list(length=None)

async def get_list_by_id(list_id: str):
    try: return await lists_collection.find_one({"_id": ObjectId(list_id)})
    except: return None

async def get_custom_fields(asin: str):
    return await custom_fields_collection.find_one({"asin": asin}, {"_id": 0})

async def save_custom_fields(asin: str, fields: dict):
    await custom_fields_collection.update_one({"asin": asin}, {"$set": fields}, upsert=True)