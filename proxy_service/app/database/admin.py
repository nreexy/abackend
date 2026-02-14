import json
import datetime
from bson.objectid import ObjectId
from .core import db

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
        s_item = json.loads(json.dumps(item, default=serialize))
        serialized_items.append(s_item)
        all_keys.update(s_item.keys())
        
    return {"keys": sorted(list(all_keys)), "items": serialized_items}
