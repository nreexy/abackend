import os
from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as redis

# --- CONFIGURATION ---
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# --- DATABASE CLIENTS ---
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client.audiobook_metadata

# Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
CACHE_TTL = 86400 # 24 Hours

# Collections
books_collection = db.books          # Main Library
custom_fields_collection = db.custom_fields
logs_collection = db.request_logs    # Activity Logs
access_logs_collection = db.access_logs # Security Logs
login_attempts_collection = db.login_attempts # Login Audit
blocked_clients_collection = db.blocked_clients # Blocklist
settings_collection = db.settings
lists_collection = db.lists
provider_stats_collection = db.provider_stats
unified_catalog_collection = db.unified_catalog
backup_jobs_collection = db.backup_jobs
backup_jobs_collection = db.backup_jobs
backup_history_collection = db.backup_history
nyt_subscriptions_collection = db.nyt_subscriptions
