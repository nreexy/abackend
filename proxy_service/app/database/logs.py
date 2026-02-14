import datetime
import hashlib
import httpx
from typing import Optional
from .core import (
    logs_collection, 
    access_logs_collection, 
    login_attempts_collection, 
    provider_stats_collection, 
    books_collection, 
    redis_client
)
from .blocklist import get_blocked_clients

# --- GEOIP UTIL ---

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

# --- LOGGING & STATS ---

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

async def get_access_logs(limit: int = 200, status_code: Optional[int] = None, blocked_only: bool = False, path_filter: str = "all"):
    """
    Fetches raw access logs for Security Tab.
    path_filter: 'all', 'default', 'non_default'
    """
    query = {}
    
    # 1. Status Filter
    if status_code is not None:
        query["status_code"] = status_code

    # 2. Blocked Filter
    blocked_clients = await get_blocked_clients()
    blocked_ips = {b['value'] for b in blocked_clients if b['type'] == 'ip'}
    
    if blocked_only:
        query["ip"] = {"$in": list(blocked_ips)}
        
    # 3. Path Filter
    # Default Paths Regex Pattern
    default_pattern = "^/ping|^/settings|^/dashboard|^/stats|^/details|^/search|^/documentation|^/library|^/lists|^/detail_view|^/database"
    
    if path_filter == "default":
        query["path"] = {"$regex": default_pattern}
    elif path_filter == "non_default":
        query["path"] = {"$not": {"$regex": default_pattern}}
        
    logs = await access_logs_collection.find(query).sort("timestamp", -1).limit(limit).to_list(length=limit)
    
    # Add Flag Emoji & Block Status
    for log in logs:
        ip = log.get("ip")
        
        # Check Block Status
        log["is_blocked"] = ip in blocked_ips
        
        cc = log.get("country_code")
        if cc:
            # Convert 2-letter country code to flag emoji
            # Regional Indicator Symbol A is 0x1F1E6 (127462)
            # 'A' is 65. Offset = 127462 - 65 = 127397
            log["flag"] = "".join([chr(ord(c) + 127397) for c in cc.upper()])
            
    return logs

# --- STATS AGGREGATION ---

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

# --- LOGIN AUDIT LOGIC ---
async def log_login_attempt(ip: str, user_agent: str, username: str, password_attempt: str, status: str, reason: str = None):
    """
    Logs a login attempt.
    Status: 'Success' or 'Failed'
    If Success, password_attempt is masked.
    """
    
    # Get Country Code (reuse existing cache)
    country_code = await get_country_code(ip)
    
    # Mask password if success
    if status == "Success":
        masked_password = "***"
    else:
        # User requested to see the password on failure. 
        # We will truncate it if it's too long to avoid DB bloat/issues
        masked_password = password_attempt[:50] if password_attempt else ""

    log_entry = {
        "timestamp": datetime.datetime.utcnow(),
        "ip": ip,
        "country": country_code,
        "user_agent": user_agent,
        "username": username,
        "password_attempt": masked_password,
        "status": status,
        "reason": reason
    }
    
    await login_attempts_collection.insert_one(log_entry)

async def get_login_attempts(limit: int = 50):
    """Returns recent login attempts."""
    cursor = login_attempts_collection.find().sort("timestamp", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    
    # Fetch Blocklist
    blocked_clients = await get_blocked_clients()
    blocked_ips = {b['value'] for b in blocked_clients if b['type'] == 'ip'}

    # Convert ObjectIds and Timestamps for JSON
    results = []
    for log in logs:
        log["_id"] = str(log["_id"])
        
        # Check Block Status
        log["is_blocked"] = log.get("ip") in blocked_ips

        # Format timestamp for UI
        if isinstance(log.get("timestamp"), datetime.datetime):
             log["timestamp_str"] = log["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        else:
             log["timestamp_str"] = "N/A"
        results.append(log)
        
    return results
