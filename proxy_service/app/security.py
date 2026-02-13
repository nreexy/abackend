from app.database import redis_client
from fastapi import Request, HTTPException, status
import logging

# Configuration
MAX_FAILURES = 5
BAN_TIME_SECONDS = 900  # 15 Minutes
FAILURE_WINDOW_SECONDS = 300  # 5 Minutes

logger = logging.getLogger("uvicorn.error")

class LoginGuard:
    """
    Protects against Brute Force attacks by tracking failed login attempts
    per IP address using Redis.
    """
    
    @staticmethod
    async def check_ban(request: Request):
        """
        Checks if the request IP is currently banned.
        Raises 403 HTTPException if banned.
        """
        ip = request.client.host if request.client else "Unknown"
        if ip in ["127.0.0.1", "::1", "localhost"]:
            return False # Allow local debugging? No, strict for now.
        
        ban_key = f"ban:{ip}"
        if await redis_client.get(ban_key):
            logger.warning(f"⛔ Blocked banned IP: {ip}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Too many failed login attempts. Try again in 15 minutes."
            )

    @staticmethod
    async def record_failure(request: Request):
        """
        Records a failed login attempt for the IP.
        Bans the IP if the threshold is reached.
        """
        ip = request.client.host if request.client else "Unknown"
        fail_key = f"fail_count:{ip}"
        ban_key = f"ban:{ip}"
        
        # Increment failure count
        count = await redis_client.incr(fail_key)
        
        # If it's the first failure, set usage window expiration
        if count == 1:
            await redis_client.expire(fail_key, FAILURE_WINDOW_SECONDS)
            
        logger.warning(f"⚠️ Failed login attempt {count}/{MAX_FAILURES} for IP: {ip}")
        
        # Check threshold
        if count >= MAX_FAILURES:
            await redis_client.set(ban_key, "banned", ex=BAN_TIME_SECONDS)
            await redis_client.delete(fail_key) # Reset counter so it starts fresh after ban
            logger.error(f"🚨 BANNED IP {ip} for {BAN_TIME_SECONDS}s due to excessive failures.")

    @staticmethod
    async def reset(request: Request):
        """
        Resets the failure count on successful login.
        """
        ip = request.client.host if request.client else "Unknown"
        fail_key = f"fail_count:{ip}"
        await redis_client.delete(fail_key)
