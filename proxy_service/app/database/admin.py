import csv
import io
import json
import datetime
from bson.objectid import ObjectId
from .core import db

# --- ADMIN / DB VIEWER ---

def _serialize(obj):
    if isinstance(obj, ObjectId): return str(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
    return obj

async def get_collection_names():
    return await db.list_collection_names()

async def execute_admin_query(collection_name: str, query: dict, limit: int = 50):
    coll = db[collection_name]
    cursor = coll.find(query).limit(limit)
    items = await cursor.to_list(length=limit)

    # To ensure consistent table columns, find all possible keys
    all_keys = set()
    serialized_items = []

    # 1. Serialize and Collect Keys
    for item in items:
        # Pymongo returns native types, convert to friendly dict
        s_item = json.loads(json.dumps(item, default=_serialize))
        serialized_items.append(s_item)
        all_keys.update(s_item.keys())

    return {"keys": sorted(list(all_keys)), "items": serialized_items}

async def export_collection_to_csv(collection_name: str) -> io.StringIO:
    """
    Fetches every document in a collection and renders it as CSV.
    Nested objects/arrays are serialized to JSON strings within their cell.
    """
    coll = db[collection_name]
    cursor = coll.find({})
    items = await cursor.to_list(length=None)

    all_keys = set()
    serialized_items = []

    for item in items:
        s_item = json.loads(json.dumps(item, default=_serialize))
        serialized_items.append(s_item)
        all_keys.update(s_item.keys())

    # Keep _id first for readability, then remaining keys sorted
    fieldnames = (["_id"] if "_id" in all_keys else []) + sorted(all_keys - {"_id"})

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for row in serialized_items:
        flat_row = {}
        for key in fieldnames:
            value = row.get(key)
            if isinstance(value, (dict, list)):
                flat_row[key] = json.dumps(value)
            elif value is None:
                flat_row[key] = ""
            else:
                flat_row[key] = value
        writer.writerow(flat_row)

    buffer.seek(0)
    return buffer
