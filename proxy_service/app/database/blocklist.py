import json
import datetime
from .core import blocked_clients_collection, redis_client

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
