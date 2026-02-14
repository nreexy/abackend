import os
import json
import datetime
from .core import settings_collection, redis_client

# --- SETTINGS ---

DEFAULT_SETTINGS = {
    "providers": {"audible": True, "itunes": True, "goodreads": True, "prh": True, "google": False, "hardcover": False},
    "search_limit": 5, 
    "scrape_limit_pages": 100,
    "google_books_api_key": os.getenv("GOOGLE_BOOKS_API_KEY", ""),
    "prh_api_key": os.getenv("PRH_API_KEY", ""),
    "hardcover_api_key": os.getenv("HARDCOVER_API_KEY", ""),
    "nyt_api_key": os.getenv("NYT_API_KEY", ""),
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
    def json_serial(obj):
        if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
        
    await redis_client.set(cache_key, json.dumps(config, default=json_serial), ex=300)
    
    return config

async def save_system_settings(providers: dict, search_limit: int, scrape_limit_pages: int, google_books_api_key: str = None, prh_api_key: str = None, hardcover_api_key: str = None, nyt_api_key: str = None, static_api_key: str = None, security: dict = None):
    update_fields = {
        "providers": providers, 
        "search_limit": search_limit, 
        "scrape_limit_pages": scrape_limit_pages
    }
    
    # Only update API key if provided
    if google_books_api_key is not None: update_fields["google_books_api_key"] = google_books_api_key
    if prh_api_key is not None: update_fields["prh_api_key"] = prh_api_key
    if hardcover_api_key is not None: update_fields["hardcover_api_key"] = hardcover_api_key
    if nyt_api_key is not None: update_fields["nyt_api_key"] = nyt_api_key
    if static_api_key is not None: update_fields["static_api_key"] = static_api_key
    if security is not None: update_fields["security"] = security

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
