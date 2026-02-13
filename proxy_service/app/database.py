from typing import Optional
import os
import json
import datetime
import uuid
import hashlib
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as redis
from bson.objectid import ObjectId
from pymongo import ASCENDING, DESCENDING

# --- CONFIGURATION ---
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# --- DATABASE CLIENTS ---
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.audiobook_metadata

# Collections
books_collection = db.books          # Main Library
custom_fields_collection = db.custom_fields
logs_collection = db.request_logs    # Activity Logs
access_logs_collection = db.access_logs # NEW: Security Logs
blocked_clients_collection = db.blocked_clients # NEW: Blocklist
settings_collection = db.settings
lists_collection = db.lists
# ... (existing code)

# --- BLOCKLIST LOGIC ---
async def get_blocked_clients():
    """Returns all blocked clients."""
    return await blocked_clients_collection.find().sort("added_at", -1).to_list(length=1000)

async def add_block(value: str, block_type: str, reason: str = "Manual Ban"):
    """
    Adds an IP or User-Agent to the blocklist.
    block_type: 'ip' or 'user_agent'
    """
    # Check if already blocked
    existing = await blocked_clients_collection.find_one({"value": value})
    if existing:
        return False
        
    await blocked_clients_collection.insert_one({
        "value": value,
        "type": block_type,
        "reason": reason,
        "added_at": datetime.datetime.utcnow()
    })
    
    # Invalidate Cache
    await redis_client.delete("blocklist_cache")
    return True

async def remove_block(value: str):
    """Removes a block."""
    result = await blocked_clients_collection.delete_one({"value": value})
    # Invalidate Cache
    await redis_client.delete("blocklist_cache")
    return result.deleted_count > 0

async def is_client_blocked(ip: str, user_agent: str) -> bool:
    """
    Checks if an IP or User-Agent is blocked.
    Uses Redis caching to avoid DB hits on every request.
    """
    # 1. Check Redis Cache
    cached_blocks = await redis_client.get("blocklist_cache")
    if cached_blocks:
        blocks = json.loads(cached_blocks)
    else:
        # 2. Fetch from DB
        cursor = blocked_clients_collection.find({}, {"_id": 0, "value": 1, "type": 1})
        blocks = await cursor.to_list(length=1000)
        # Cache for 1 minute (or until update)
        await redis_client.setex("blocklist_cache", 60, json.dumps(blocks))
        
    # 3. Check against list
    for block in blocks:
        if block["type"] == "ip" and block["value"] == ip:
            return True
        if block["type"] == "user_agent" and block["value"] in user_agent:
             return True
             
    return False
provider_stats_collection = db.provider_stats
unified_catalog_collection = db.unified_catalog

# Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
CACHE_TTL = 86400 # 24 Hours

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
    Atomically increments the access_count and updates last_accessed.
    """
    now = datetime.datetime.utcnow()
    await books_collection.update_one(
        {"asin": asin},
        {
            "$inc": {"access_count": 1},
            "$set": {"last_accessed": now}
        }
    )

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
        
        item_type = "Search" if "search" in key else "Book" if "book" in key else "Data"
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

# --- SETTINGS ---

DEFAULT_SETTINGS = {
    "providers": {"audible": True, "itunes": True, "goodreads": True, "prh": True, "google": False, "hardcover": False},
    "search_limit": 5, 
    "scrape_limit_pages": 100,
    "google_books_api_key": os.getenv("GOOGLE_BOOKS_API_KEY", ""),
    "prh_api_key": os.getenv("PRH_API_KEY", ""),
    "hardcover_api_key": os.getenv("HARDCOVER_API_KEY", ""),
    "static_api_key": None,
    "security": {
        "enable_country_block": False,
        "allowed_countries": ["DE"], 
        "enable_ua_block": False,
        "required_ua_keywords": ["Macintosh"], 
        "enable_strict_anti_scan": True 
    }
}

async def get_system_settings():
    # 1. Try Cache
    cache_key = "system_settings_cache"
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    # 2. Fetch DB
    config = await settings_collection.find_one({"_id": "global_config"})
    if not config:
        config = DEFAULT_SETTINGS.copy()
    else:
        # Deep merge: ensure new DEFAULT_SETTINGS keys exist in stored config
        for key, default_val in DEFAULT_SETTINGS.items():
            if key not in config:
                config[key] = default_val
            elif isinstance(default_val, dict) and isinstance(config.get(key), dict):
                # Merge nested dicts (e.g. security, providers)
                for sub_key, sub_val in default_val.items():
                    if sub_key not in config[key]:
                        config[key][sub_key] = sub_val
    
    # 3. Set Cache (300s = 5 mins)
    # Convert ObjectId to str if present (though settings usually don't have generated _id other than set string)
    # using default serializer just in case
    def json_serial(obj):
        if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
        
    await redis_client.set(cache_key, json.dumps(config, default=json_serial), ex=300)
    
    return config

async def save_system_settings(providers: dict, search_limit: int, scrape_limit_pages: int, google_books_api_key: str = None, prh_api_key: str = None, hardcover_api_key: str = None, static_api_key: str = None, security: dict = None):
    update_fields = {
        "providers": providers, 
        "search_limit": search_limit, 
        "scrape_limit_pages": scrape_limit_pages
    }
    
    # Only update API key if provided (or explicitly cleared if passed as empty string, 
    # but usually we want to preserve it if not passed in main form)
    if google_books_api_key is not None:
        update_fields["google_books_api_key"] = google_books_api_key

    if prh_api_key is not None:
        update_fields["prh_api_key"] = prh_api_key

    if hardcover_api_key is not None:
        update_fields["hardcover_api_key"] = hardcover_api_key
        
    if static_api_key is not None:
        update_fields["static_api_key"] = static_api_key

    if security is not None:
        update_fields["security"] = security

    await settings_collection.update_one(
        {"_id": "global_config"},
        {"$set": update_fields},
        upsert=True
    )
    
    # Invalidate Cache
    await redis_client.delete("system_settings_cache")

async def get_stored_password_hash():
    """Retrieves the admin password hash from the database."""
    config = await settings_collection.find_one({"_id": "auth_config"})
    return config.get("password_hash") if config else None

async def set_stored_password_hash(hash_str: str):
    """Saves the admin password hash to the database."""
    await settings_collection.update_one(
        {"_id": "auth_config"},
        {"$set": {"password_hash": hash_str, "updated_at": datetime.datetime.utcnow()}},
        upsert=True
    )

async def clear_stored_password_hash():
    """Removes the admin password hash from the database."""
    await settings_collection.update_one(
        {"_id": "auth_config"},
        {"$unset": {"password_hash": ""}}
    )

# --- CUSTOM FIELDS ---
async def get_custom_fields(asin: str):
    return await custom_fields_collection.find_one({"asin": asin}, {"_id": 0})

async def save_custom_fields(asin: str, fields: dict):
    await custom_fields_collection.update_one({"asin": asin}, {"$set": fields}, upsert=True)

# --- LOGGING & STATS (Unified) ---

async def log_activity(action: str, target: str, details: str = None, device_id: str = "Unknown", country: str = "Unknown", duration_ms: float = 0.0, ip: str = None):
    """
    Logs activity with Debouncing logic.
    Accepts either 'device_id' (hashed) or 'ip' (legacy/fallback).
    """
    # Use IP as fallback ID if device_id not passed
    final_id = device_id if device_id != "Unknown" else (ip or "Unknown")
    
    # 1. Debounce Hash
    raw_key = f"{final_id}:{action}:{target}"
    log_hash = hashlib.md5(raw_key.encode()).hexdigest()
    debounce_key = f"log_debounce:{log_hash}"

    # 2. Atomic Lock check (5 seconds)
    is_new = await redis_client.set(debounce_key, "1", ex=5, nx=True)
    if not is_new: return

    # 3. Log
    await logs_collection.insert_one({
        "timestamp": datetime.datetime.utcnow(),
        "action": action,
        "target": target,
        "details": details,
        "device_id": final_id,
        "country": country,
        "duration_ms": duration_ms,
        # Keep 'ip' field for legacy compatibility if needed, but prefer device_id
        "ip": ip if ip else final_id 
    })

async def log_provider_stats(request_id: str, provider: str, duration_ms: float, result_count: int, status: str):
    await provider_stats_collection.insert_one({
        "timestamp": datetime.datetime.utcnow(),
        "request_id": request_id, "provider": provider,
        "duration_ms": duration_ms, "result_count": result_count, "status": status
    })

async def get_system_logs(limit: int = 100):
    return await logs_collection.find().sort("timestamp", -1).limit(limit).to_list(length=limit)

async def get_country_code(ip: str) -> Optional[str]:
    """
    Resolves IP to 2-letter Country Code using ip-api.com.
    Caches results in Redis for 24 hours.
    Returns None if private IP or lookup fails.
    """
    # 0. Skip Local/Private IPs (basic check)
    if ip in ["127.0.0.1", "localhost", "::1"] or ip.startswith("192.168.") or ip.startswith("10."):
        return None
        
    # 1. Check Redis Cache
    cache_key = f"ip_country:{ip}"
    cached_code = await redis_client.get(cache_key)
    if cached_code:
        return cached_code if cached_code != "Unknown" else None

    # 2. External API Lookup
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=countryCode", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("countryCode")
                if code:
                    # Cache Success (24h)
                    await redis_client.set(cache_key, code, ex=86400)
                    return code
    except Exception as e:
        print(f"GeoIP Lookup Failed for {ip}: {e}")
        pass

    # 3. Cache Failure (to avoid retry storms)
    await redis_client.set(cache_key, "Unknown", ex=3600) # Cache failure for 1h
    return None

async def log_request_access(data: dict):
    """
    Logs raw request access for Security Audit.
    """
    # Enrich with GeoIP
    ip = data.get("ip")
    if ip:
        country_code = await get_country_code(ip)
        if country_code:
            data["country_code"] = country_code

    # Optional: Cap collection size? For now, just insert.
    await access_logs_collection.insert_one(data)

async def get_access_logs(limit: int = 100, status_code: Optional[int] = None):
    """
    Fetches raw access logs for Security Tab.
    """
    query = {}
    if status_code is not None:
        query["status_code"] = status_code
        
    logs = await access_logs_collection.find(query).sort("timestamp", -1).limit(limit).to_list(length=limit)
    
    # Add Flag Emoji
    for log in logs:
        cc = log.get("country_code")
        if cc:
            # Convert 2-letter country code to flag emoji
            # Regional Indicator Symbol A is 0x1F1E6 (127462)
            # 'A' is 65. Offset = 127462 - 65 = 127397
            log["flag"] = "".join([chr(ord(c) + 127397) for c in cc.upper()])
            
    return logs

async def get_traffic_stats():
    total_requests = await logs_collection.count_documents({})

    # 1. Distinct Devices
    pipeline_devices = [{"$group": {"_id": "$device_id"}}, {"$count": "count"}]
    dev_res = await logs_collection.aggregate(pipeline_devices).to_list(length=1)
    distinct_devices = dev_res[0]["count"] if dev_res else 0

    # 2. Country Stats
    pipeline_geo = [
        {"$group": {"_id": {"$ifNull": ["$country", "Unknown"]}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    geo_groups = await logs_collection.aggregate(pipeline_geo).to_list(length=None)

    sorted_countries = []
    for entry in geo_groups:
        code = entry["_id"]
        count = entry["count"]
        # Filter out internal/unknown if desired
        percent = round((count/total_requests)*100, 1) if total_requests > 0 else 0
        sorted_countries.append({"code": code, "count": count, "percent": percent})

    avg_per_device = round(total_requests / distinct_devices, 2) if distinct_devices else 0
    
    # 3. Logs
    recent_logs = await logs_collection.find().sort("timestamp", -1).limit(100).to_list(length=100)

    return {
        "total_requests": total_requests,
        "distinct_devices": distinct_devices,
        "avg_per_device": avg_per_device,
        "countries": sorted_countries,
        "logs": recent_logs
    }

async def get_detailed_stats():
    pipeline = [
        {"$group": {"_id": "$provider", "total_calls": {"$sum": 1}, "total_results": {"$sum": "$result_count"}, "avg_latency": {"$avg": "$duration_ms"}, "successful_calls": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}}}},
        {"$sort": {"total_calls": -1}}
    ]
    stats = await provider_stats_collection.aggregate(pipeline).to_list(length=None)
    recent = await provider_stats_collection.find().sort("timestamp", -1).limit(50).to_list(length=50)
    return {"aggregated": stats, "recent": recent}

async def get_dashboard_stats():
    # 1. Basic Counts
    total_requests = await logs_collection.count_documents({})
    books_count = await books_collection.count_documents({})
    # lists_count = await lists_collection.count_documents({}) # Unused
    ping_count = await access_logs_collection.count_documents({"path": "/ping"})

    # 2. Unique Visitors (Simple approximation via distinct IP)
    # Note: On large datasets, distinct can be slow. For now it's fine.
    unique_ips = await logs_collection.distinct("ip")
    total_visitors = len(unique_ips)

    # 3. Top Books Pipeline
    pipeline = [
        {"$match": {"action": "fetch_metadata"}},
        {"$group": {"_id": "$target", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8}, # Limit to top 8 for UI
        # Join with books to get real title
        {"$lookup": {
            "from": "books",
            "localField": "_id",
            "foreignField": "asin",
            "as": "book_info"
        }},
        # Extract title (fallback to ASIN if missing)
        {"$project": {
            "_id": 1, 
            "count": 1,
            "title": {"$ifNull": [{"$arrayElemAt": ["$book_info.title", 0]}, "$_id"]},
            "authors": {"$ifNull": [{"$arrayElemAt": ["$book_info.authors", 0]}, []]} # Get authors for UI
        }}
    ]
    
    top_books = await logs_collection.aggregate(pipeline).to_list(length=8)
    
    return {
        "total_requests": total_requests, 
        "total_books": books_count,
        "total_pings": ping_count,
        "total_visitors": total_visitors,
        "top_books": top_books
    }
# --- LISTS LOGIC ---

async def save_imported_list(name: str, url: str, asins: list, source: str = "Audible"):
    doc = {
        "name": name, "url": url, "asins": asins, "count": len(asins),
        "type": "imported", "source": source,
        "created_at": datetime.datetime.utcnow(),
        "updated_at": datetime.datetime.utcnow()
    }
    await lists_collection.update_one({"url": url}, {"$set": doc}, upsert=True)

async def create_custom_list(name: str, asins: list):
    internal_id = f"custom:{uuid.uuid4()}"
    doc = {
        "name": name, "url": internal_id, "asins": asins, "count": len(asins),
        "type": "custom", "source": "Custom",
        "created_at": datetime.datetime.utcnow(),
        "updated_at": datetime.datetime.utcnow()
    }
    await lists_collection.insert_one(doc)
    return internal_id

async def get_all_lists():
    return await lists_collection.find().sort("created_at", -1).to_list(length=None)

async def get_list_by_id(list_id: str):
    try: return await lists_collection.find_one({"_id": ObjectId(list_id)})
    except: return None

async def delete_list_by_id(list_id: str):
    try:
        await lists_collection.delete_one({"_id": ObjectId(list_id)})
        return True
    except: return False

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

# --- ADMIN / DB VIEWER ---
async def get_collection_names():
    return await db.list_collection_names()

async def execute_admin_query(collection_name: str, query: dict, limit: int = 50):
    coll = db[collection_name]
    cursor = coll.find(query).limit(limit)
    items = await cursor.to_list(length=limit)
    
    # Serialize Objects (ObjectId, datetime)
    def serialize(obj):
        if isinstance(obj, ObjectId): return str(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
        return obj

    # To ensure consistent table columns, find all possible keys
    all_keys = set()
    serialized_items = []
    
    # 1. Serialize and Collect Keys
    for item in items:
        # Pymongo returns native types, convert to friendly dict
        # Flattening simple nested objects could be nice, but raw JSON is safer for viewer
        # We will just serialize the values for display
        s_item = json.loads(json.dumps(item, default=serialize))
        serialized_items.append(s_item)
        all_keys.update(s_item.keys())
        
    return {"keys": sorted(list(all_keys)), "items": serialized_items}