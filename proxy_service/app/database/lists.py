import datetime
import uuid
from bson.objectid import ObjectId
from .core import lists_collection, redis_client

CURATION_LISTS_CACHE_KEY = "curation:lists:v1"

def _items_cache_key(list_id: str, page: int, limit: int) -> str:
    return f"curation:items:v1:{list_id}:p{page}:l{limit}"

async def _invalidate_list_caches(list_id: str):
    """Delete the lists overview cache and all pages of this list's items cache."""
    await redis_client.delete(CURATION_LISTS_CACHE_KEY)
    # Delete all cached pages for this list via a scan on the prefix
    prefix = f"curation:items:v1:{list_id}:*"
    keys = [k async for k in redis_client.scan_iter(prefix)]
    if keys:
        await redis_client.delete(*keys)

# --- LISTS LOGIC ---

async def save_imported_list(name: str, url: str, asins: list, source: str = "Audible", raw_items: list = None):
    doc = {
        "name": name, "url": url, "asins": asins, "count": len(asins) if not raw_items else len(raw_items),
        "type": "imported", "source": source,
        # Store raw items for lists without ASINs (e.g. NYT)
        "items": raw_items or [],
        "created_at": datetime.datetime.utcnow(),
        "updated_at": datetime.datetime.utcnow()
    }
    await lists_collection.update_one({"url": url}, {"$set": doc}, upsert=True)

async def create_custom_list(name: str, asins: list):
    internal_id = f"custom:{uuid.uuid4()}"
    doc = {
        "name": name, "url": internal_id, "asins": asins, "count": len(asins),
        "type": "custom", "source": "Custom",
        "language": None, "hidden": False,
        "created_at": datetime.datetime.utcnow(),
        "updated_at": datetime.datetime.utcnow()
    }
    await lists_collection.insert_one(doc)
    return internal_id

async def update_list_metadata(list_id: str, language: str = None, hidden: bool = None):
    fields = {"updated_at": datetime.datetime.utcnow()}
    if language is not None:
        fields["language"] = language or None
    if hidden is not None:
        fields["hidden"] = hidden
    try:
        await lists_collection.update_one({"_id": ObjectId(list_id)}, {"$set": fields})
        await _invalidate_list_caches(list_id)
        return True
    except:
        return False

async def get_all_lists():
    return await lists_collection.find().sort("created_at", -1).to_list(length=None)

async def get_list_by_id(list_id: str):
    try: return await lists_collection.find_one({"_id": ObjectId(list_id)})
    except: return None

async def delete_list_by_id(list_id: str):
    try:
        await lists_collection.delete_one({"_id": ObjectId(list_id)})
        await _invalidate_list_caches(list_id)
        return True
    except: return False

async def update_list_name(list_id: str, new_name: str):
    try:
        await lists_collection.update_one(
            {"_id": ObjectId(list_id)},
            {"$set": {"name": new_name, "updated_at": datetime.datetime.utcnow()}}
        )
        await _invalidate_list_caches(list_id)
        return True
    except: return False

async def set_item_note(list_id: str, asin: str, note: str):
    try:
        await lists_collection.update_one(
            {"_id": ObjectId(list_id)},
            {"$set": {f"notes.{asin}": note.strip(), "updated_at": datetime.datetime.utcnow()}}
        )
        await _invalidate_list_caches(list_id)
        return True
    except:
        return False

async def get_item_note(list_id: str, asin: str) -> str:
    try:
        doc = await lists_collection.find_one({"_id": ObjectId(list_id)}, {"notes": 1})
        return (doc or {}).get("notes", {}).get(asin, "")
    except:
        return ""

async def add_item_to_list(list_id: str, asin: str):
    try:
        updated = await lists_collection.find_one_and_update(
            {"_id": ObjectId(list_id)},
            {
                "$addToSet": {"asins": asin},
                "$set": {"updated_at": datetime.datetime.utcnow()}
            },
            return_document=True,
            projection={"asins": 1}
        )
        if updated:
            await lists_collection.update_one(
                {"_id": ObjectId(list_id)},
                {"$set": {"count": len(updated.get("asins", []))}}
            )
        await _invalidate_list_caches(list_id)
        return True
    except: return False

async def remove_item_from_list(list_id: str, asin: str):
    try:
        updated = await lists_collection.find_one_and_update(
            {"_id": ObjectId(list_id)},
            {
                "$pull": {"asins": asin},
                "$set": {"updated_at": datetime.datetime.utcnow()}
            },
            return_document=True,
            projection={"asins": 1}
        )
        if updated:
            await lists_collection.update_one(
                {"_id": ObjectId(list_id)},
                {"$set": {"count": len(updated.get("asins", []))}}
            )
        await _invalidate_list_caches(list_id)
        return True
    except: return False
