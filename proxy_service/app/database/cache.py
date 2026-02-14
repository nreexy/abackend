import json
import datetime
from .core import redis_client, CACHE_TTL

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
