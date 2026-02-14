from fastapi import Request, HTTPException
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user

# Shared Templates Instance
templates = Jinja2Templates(directory="templates")

async def check_ui_auth(request: Request):
    """
    Helper to verify auth for UI routes. 
    Returns True if authorized, False if not (triggering redirect).
    """
    try:
        await get_current_user(request)
    except HTTPException:
        # If system not initialized or invalid token, return False
        return False
    return True
