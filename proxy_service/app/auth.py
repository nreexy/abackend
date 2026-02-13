import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import SECRET_KEY, ALGORITHM, ADMIN_USERNAME, ADMIN_PASSWORD_HASH, ENABLE_PASSWORD_RESET_FILE

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 Scheme (Standard API Header check)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=7)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

from app.database import get_stored_password_hash, clear_stored_password_hash, get_system_settings
from app.security import LoginGuard

# --- HELPER: Get Active Password Hash ---
async def get_active_password_hash():
    """
    Returns the active password hash.
    Priority:
    1. Environment Variable (config.ADMIN_PASSWORD_HASH)
    2. Database (settings_collection)
    """
    if ADMIN_PASSWORD_HASH:
        return ADMIN_PASSWORD_HASH
    
    # Check DB
    db_hash = await get_stored_password_hash()
    return db_hash

async def check_password_reset_file():
    """
    Checks for the presence of a .passreset file to trigger a password reset.
    Checks locations relative to this file:
    1. Service Root (../.passreset) - e.g. proxy_service/.passreset
    2. Repo Root (../../.passreset) - e.g. audio-metadata-server/.passreset
    
    If found and no environment password is set, clears the DB password and deletes the file.
    Returns True if reset occurred, False otherwise.
    """
    # 0. Check Toggle
    if not ENABLE_PASSWORD_RESET_FILE:
        return False

    # Calculate paths relative to app/auth.py
    # Calculate paths relative to app/auth.py
    current_dir = os.path.dirname(os.path.abspath(__file__))
    service_root = os.path.dirname(current_dir) # proxy_service
    repo_root = os.path.dirname(service_root)   # audio-metadata-server
    
    candidates = [
        os.path.join(service_root, ".passreset"),
        os.path.join(repo_root, ".passreset")
    ]
    
    found_file = None
    for path in candidates:
        if os.path.exists(path):
            found_file = path
            break
            
    if found_file:
        print(f"⚠️ Found reset file at {found_file}. Attempting password reset...")
        
        # 1. Check if ENV pass is enforced
        if ADMIN_PASSWORD_HASH and os.getenv("ADMIN_PASSWORD_HASH"):
             print("❌ Cannot reset password: ADMIN_PASSWORD_HASH is set in environment variables.")
             return False
             
        # 2. Clear DB
        await clear_stored_password_hash()
        print("✅ Password hash cleared from database.")
        
        # 3. Delete File
        try:
            os.remove(found_file)
            print(f"🗑️ Deleted {found_file}")
        except Exception as e:
            print(f"⚠️ Failed to delete {found_file}: {e}")
            
        return True
    return False

# --- DEPENDENCY: Check Auth (Cookie OR Header) ---
async def get_current_user(request: Request):
    """
    Checks for a valid token in:
    1. The 'Authorization' Header (API usage)
    2. The 'access_token' Cookie (Browser usage)
    3. STATIC API KEY Check
    """
    
    # 0. Check if system is initialized
    active_hash = await get_active_password_hash()
    if not active_hash:
        # System is not initialized (no password set)
        raise HTTPException(status_code=401, detail="System not initialized")

    # 0b. Check Ban Status (Fail2Ban Style)
    await LoginGuard.check_ban(request)

    token = None
    
    # 1. Check Header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        
        # --- NEW: Check Static API Key ---
        settings = await get_system_settings()
        static_key = settings.get("static_api_key")
        if static_key and token == static_key:
            await LoginGuard.reset(request) # Success
            return ADMIN_USERNAME
    
    # 2. Check Cookie (if no header)
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        # Don't record failure here - it just means user is not logged in yet
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None or username != ADMIN_USERNAME:
            await LoginGuard.record_failure(request)
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
        await LoginGuard.reset(request) # Success
    except JWTError:
        await LoginGuard.record_failure(request)
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return username