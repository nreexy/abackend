from slowapi import Limiter
from slowapi.util import get_remote_address
import os

# Use the redis connection string from env
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Initialize Limiter using Redis as storage
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=REDIS_URL,
    default_limits=["200/minute"] # Default global limit
)