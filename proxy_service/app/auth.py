from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import SECRET_KEY, ALGORITHM, ADMIN_USERNAME, ADMIN_PASSWORD_HASH

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

from app.database import get_stored_password_hash

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

# --- DEPENDENCY: Check Auth (Cookie OR Header) ---
async def get_current_user(request: Request):
    """
    Checks for a valid token in:
    1. The 'Authorization' Header (API usage)
    2. The 'access_token' Cookie (Browser usage)
    """
    
    # 0. Check if system is initialized
    active_hash = await get_active_password_hash()
    if not active_hash:
        # System is not initialized (no password set)
        # We raise a special error or just 401. 
        # The UI router will handle redirection to /setup if needed.
        raise HTTPException(status_code=401, detail="System not initialized")

    token = None
    
    # 1. Check Header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    
    # 2. Check Cookie (if no header)
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None or username != ADMIN_USERNAME:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return username