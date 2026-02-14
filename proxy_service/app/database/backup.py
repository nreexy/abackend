import os
import datetime
import json
import asyncio
import aiofiles
from bson import json_util
from typing import List, Optional

from .core import (
    settings_collection,
    blocked_clients_collection,
    books_collection,
    custom_fields_collection,
    unified_catalog_collection,
    logs_collection,
    access_logs_collection,
    login_attempts_collection,
    provider_stats_collection,
    lists_collection,
    backup_history_collection,
    db
)

BACKUP_DIR = "/backups"

# Ensure backup directory exists
if not os.path.exists(BACKUP_DIR):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except PermissionError:
        # Fallback for dev environment
        BACKUP_DIR = "./backups"
        os.makedirs(BACKUP_DIR, exist_ok=True)

# Map sections to collections
SECTION_MAP = {
    "settings": {
        "settings": settings_collection,
        "blocked_clients": blocked_clients_collection
    },
    "books": {
        "books": books_collection,
        "custom_fields": custom_fields_collection,
        "unified_catalog": unified_catalog_collection
    },
    "logs": {
        "logs": logs_collection,
        "access_logs": access_logs_collection,
        "login_attempts": login_attempts_collection,
        "provider_stats": provider_stats_collection
    },
    "lists": {
        "lists": lists_collection
    }
}

async def create_backup(sections: List[str], trigger: str = "manual") -> str:
    """
    Creates a backup of specified sections.
    Returns the filename of the backup.
    trigger: 'manual' or schedule_id
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.json"
    filepath = os.path.join(BACKUP_DIR, filename)
    start_time = datetime.datetime.utcnow()
    
    try:
        backup_data = {
            "metadata": {
                "timestamp": datetime.datetime.now().isoformat(),
                "sections": sections,
                "version": "1.0"
            },
            "content": {}
        }
    
        # If "full" is passed, expand it
        if "full" in sections:
            sections = list(SECTION_MAP.keys())
    
        for section in sections:
            if section in SECTION_MAP:
                backup_data["content"][section] = {}
                for coll_name, collection in SECTION_MAP[section].items():
                    # Fetch all documents
                    docs = await collection.find().to_list(length=None)
                     # Convert ObjectIds for JSON
                    docs = json.loads(json_util.dumps(docs))
                    backup_data["content"][section][coll_name] = docs
    
        # Serialize using bson.json_util to handle ObjectIds and Datetimes
        # docs are already converted above for safety in dict, but json_util.dumps handles whole structure
        json_str = json_util.dumps(backup_data, indent=2)
    
        # Write to file
        async with aiofiles.open(filepath, "w") as f:
            await f.write(json_str)

        # Log Success
        file_size = os.path.getsize(filepath)
        await backup_history_collection.insert_one({
            "timestamp": start_time,
            "action": "backup",
            "sections": sections,
            "status": "success",
            "filename": filename,
            "size_bytes": file_size,
            "trigger": trigger
        })

        return filename

    except Exception as e:
        # Log Failure
        await backup_history_collection.insert_one({
            "timestamp": start_time,
            "action": "backup",
            "sections": sections,
            "status": "failed",
            "error": str(e),
            "trigger": trigger
        })
        raise e

async def list_backups() -> List[dict]:
    """
    Lists all available backup files with metadata.
    """
    backups = []
    if not os.path.exists(BACKUP_DIR):
        return []

    for f in os.listdir(BACKUP_DIR):
        if f.endswith(".json") and f.startswith("backup_"):
            filepath = os.path.join(BACKUP_DIR, f)
            try:
                stat = os.stat(filepath)
                # Parse timestamp from filename to avoid reading file
                # backup_20260213_120000.json
                parts = f.replace("backup_", "").replace(".json", "").split("_")
                ts_str = f"{parts[0]} {parts[1]}" if len(parts) >= 2 else ""
                
                # Determine sections (peek into file? too slow. Just return basic info)
                # We can store a separate manifest if needed, but for now specific sections is detailed.
                # Let's read the first few lines to get metadata if possible? 
                # No, standard JSON parse reads whole file.
                # We will rely on filename time.
                
                backups.append({
                    "filename": f,
                    "size_bytes": stat.st_size,
                    "created_at": datetime.datetime.fromtimestamp(stat.st_mtime),
                    "timestamp_str": ts_str
                })
            except Exception as e:
                print(f"Error reading backup {f}: {e}")
                continue
    
    # Sort by new
    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups

async def restore_backup(filename: str, sections_to_restore: List[str] = None) -> bool:
    """
    Restores data from a backup file.
    If sections_to_restore is None, restores everything in the backup.
    """
    filepath = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(filepath):
        return False

    try:
        async with aiofiles.open(filepath, "r") as f:
            content = await f.read()
            
        data = json_util.loads(content)
        backup_content = data.get("content", {})
        
        # Determine what to restore
        if sections_to_restore is None or "full" in sections_to_restore:
            # Restore all present sections
            targets = backup_content.keys()
        else:
            targets = sections_to_restore

        for section in targets:
            if section in backup_content:
                section_data = backup_content[section]
                
                # Restore each collection in the section
                for coll_name, docs in section_data.items():
                    # Find the actual collection object
                    target_collection = None
                    if section in SECTION_MAP and coll_name in SECTION_MAP[section]:
                        target_collection = SECTION_MAP[section][coll_name]
                    
                    if target_collection is not None:
                        # Clear existing data and restore
                        await target_collection.delete_many({})
                        if docs:
                            await target_collection.insert_many(docs)
                            
        return True
    except Exception as e:
        print(f"Restore failed: {e}")
        return False

async def get_backup_history(limit: int = 50):
    """Returns recent backup history entries."""
    cursor = backup_history_collection.find().sort("timestamp", -1).limit(limit)
    history = await cursor.to_list(length=limit)
    
    # Format for UI
    results = []
    for h in history:
        h["_id"] = str(h["_id"])
        if isinstance(h.get("timestamp"), datetime.datetime):
             h["timestamp_str"] = h["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        results.append(h)
    return results

async def delete_backup(filename: str) -> bool:
    filepath = os.path.join(BACKUP_DIR, filename)
    file_deleted = False
    if os.path.exists(filepath):
        os.remove(filepath)
        file_deleted = True
        
    # Also remove from history
    db_result = await backup_history_collection.delete_one({"filename": filename})
    
    return file_deleted or db_result.deleted_count > 0

async def cleanup_old_backups(retention_days: int) -> int:
    """
    Deletes backups older than retention_days.
    Returns number of deleted files.
    """
    if retention_days <= 0:
        return 0
        
    now = datetime.datetime.now()
    deleted_count = 0
    
    backups = await list_backups()
    for b in backups:
        age = now - b["created_at"]
        if age.days > retention_days:
            await delete_backup(b["filename"])
            deleted_count += 1
            
    return deleted_count
