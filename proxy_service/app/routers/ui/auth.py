from fastapi import APIRouter, Request, Form, Response, status
from starlette.responses import HTMLResponse, RedirectResponse

from app.auth import (
    verify_password, create_access_token, ADMIN_USERNAME, 
    get_active_password_hash, get_password_hash, check_password_reset_file
)
from app.database import (
    set_stored_password_hash, 
    log_login_attempt # NEW: Login Audit
)
from app.security import LoginGuard
from app.limiter import limiter
from .utils import templates

router = APIRouter()

# --- SETUP ---
@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if await get_active_password_hash():
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html")

@router.post("/setup")
async def setup_action(
    request: Request, 
    password: str = Form(...), 
    confirm_password: str = Form(...)
):
    if await get_active_password_hash():
        return RedirectResponse(url="/login", status_code=303)

    if password != confirm_password:
        return templates.TemplateResponse(request, "setup.html", {"error": "Passwords do not match"})
    
    if len(password) < 8:
        return templates.TemplateResponse(request, "setup.html", {"error": "Password must be at least 8 characters"})

    # Hash and Save
    pw_hash = get_password_hash(password)
    await set_stored_password_hash(pw_hash)
    
    return RedirectResponse(url="/login?setup=success", status_code=303)

# --- LOGIN / LOGOUT ---

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Check if setup needed
    if not await get_active_password_hash():
        return RedirectResponse(url="/setup", status_code=303)
    
    # Check for Reset File (Triggers Reset if present)
    if await check_password_reset_file():
        return RedirectResponse(url="/setup?reset=true", status_code=303)
        
    return templates.TemplateResponse(request, "login.html")

@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, response: Response, username: str = Form(...), password: str = Form(...)):
    # 0. Check Ban Status
    await LoginGuard.check_ban(request)

    active_hash = await get_active_password_hash()
    
    if not active_hash:
        return RedirectResponse(url="/setup", status_code=303)

    # Extract info for logging
    client_ip = request.client.host if request.client else "Unknown"
    # Handle X-Forwarded-For if behind proxy
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    user_agent = request.headers.get("user-agent", "Unknown")

    if username == ADMIN_USERNAME and verify_password(password, active_hash):
        # Success -> Reset Fail Count
        await LoginGuard.reset(request)
        
        # Log Success
        await log_login_attempt(client_ip, user_agent, username, password, "Success")
        
        # Create Token
        access_token = create_access_token(data={"sub": username})
        # Set Cookie (HttpOnly)
        resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        
        # HTTPS Detection: secure cookie only when behind HTTPS proxy
        is_https = request.headers.get("x-forwarded-proto", "http") == "https"
        
        resp.set_cookie(
            key="access_token", 
            value=access_token, 
            httponly=True,   # JavaScript cannot read it
            secure=is_https, # Auto-detect: True behind HTTPS, False on local dev
            samesite="lax",  # Protects against CSRF
            max_age=60*60*24*7 # 7 Days
        )
        return resp
    else:
        # Log Failure
        await log_login_attempt(client_ip, user_agent, username, password, "Failed", "Invalid Credentials")
        
        await LoginGuard.record_failure(request)
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid Username or Password"})

@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("access_token")
    return resp
