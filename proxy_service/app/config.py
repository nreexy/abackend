import os
from passlib.context import CryptContext

# --- SECURITY CONFIG ---
SECRET_KEY = os.getenv("SECRET_KEY", "change_this_to_a_random_string_in_production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 Days

# --- ADMIN CREDENTIALS ---
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")

# 1. Check for Env Var Hash
_env_hash = os.getenv("ADMIN_PASSWORD_HASH")

if _env_hash:
    # Use provided hash from environment
    ADMIN_PASSWORD_HASH = _env_hash
else:
    # 2. Generate Hash dynamically for default "admin"
    # This ensures compatibility with the installed bcrypt version
    print("🔐 Generating default admin hash...")
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    ADMIN_PASSWORD_HASH = pwd_context.hash("admin")

# --- URLS & SETTINGS ---
AUDNEXUS_URL = os.getenv("AUDNEXUS_URL", "http://audnexus:3000")
AUDIBLE_AUTH_FILE = "audible_auth.json"

# Audible Response Groups
RESPONSE_GROUPS = (
    "product_attrs,product_desc,product_extended_attrs,"
    "media,contributors,rating,series,category_ladders,"
    "sample"
)

PRH_API_KEY = os.getenv("PRH_API_KEY", "INSERT_API_KEY_HERE") 