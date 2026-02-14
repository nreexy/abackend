from fastapi import APIRouter, Request, Form, Response, Depends, HTTPException
from starlette.responses import RedirectResponse, StreamingResponse, FileResponse
from typing import Optional
import datetime

from app.database import (
    get_system_settings, 
    save_system_settings, 
    flush_all_cache, 
    get_system_logs, 
    get_access_logs, 
    get_login_attempts,
    get_blocked_clients,
    add_block, 
    remove_block,
    access_logs_collection,
    backup_jobs_collection
)
from app.database.backup import (
    create_backup, 
    restore_backup, 
    delete_backup, 
    get_backup_history,
    cleanup_old_backups,
    BACKUP_DIR
)
import os
from app.scheduler import refresh_scheduler_jobs
from app.auth import get_current_user
from .utils import templates, check_ui_auth

router = APIRouter()

# --- SETTINGS & LOGS ---

@router.get("/settings")
async def view_settings(
    request: Request, 
    status_filter: Optional[int] = None, 
    blocked_filter: bool = False,
    path_filter: str = "all"
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    config = await get_system_settings()
    
    # Retrieve the token from the cookie to show it in the UI
    token = request.cookies.get("access_token", "")
    
    # FETCH LOGS
    system_logs = await get_system_logs(limit=200)
    access_logs = await get_access_logs(
        limit=200, 
        status_code=status_filter, 
        blocked_only=blocked_filter,
        path_filter=path_filter
    )
    login_logs = await get_login_attempts(limit=50) 
    blocks = await get_blocked_clients()
    
    # BACKUP DATA
    backup_history = await get_backup_history(limit=20)
    backup_jobs = await backup_jobs_collection.find().to_list(length=None)
    
    return templates.TemplateResponse("settings.html", {
        "request": request, 
        "config": config, 
        "active_page": "settings",
        "api_token": token,
        "system_logs": system_logs, 
        "access_logs": access_logs,
        "login_logs": login_logs, 
        "current_status_filter": status_filter,
        "current_blocked_filter": blocked_filter,
        "current_path_filter": path_filter,
        "blocked_clients": blocks,
        "backup_history": backup_history,
        "backup_jobs": backup_jobs
    })

@router.post("/settings/update")
async def update_settings(
    request: Request,
    limit: int = Form(5),
    scrape_limit: int = Form(100),
    prov_audible: bool = Form(False),
    prov_itunes: bool = Form(False),
    prov_goodreads: bool = Form(False),
    prov_prh: bool = Form(False),
    prov_google: bool = Form(False),
    google_books_api_key: str = Form(None),
    prh_api_key: str = Form(None),
    hardcover_api_key: str = Form(None),
    nyt_api_key: str = Form(None),
    static_api_key: str = Form(None), 
    prov_hardcover: bool = Form(False),
    preserve_settings: bool = Form(False),
    # Clear Flags
    clear_google_books_api_key: bool = Form(False),
    clear_prh_api_key: bool = Form(False),
    clear_hardcover_api_key: bool = Form(False),
    clear_nyt_api_key: bool = Form(False),
    clear_static_api_key: bool = Form(False),
    # Security Settings
    sec_enable_country: bool = Form(False),
    sec_allowed_countries: str = Form("DE"),
    sec_enable_ua: bool = Form(False),
    sec_required_ua: str = Form("Macintosh"),
    sec_anti_scan: bool = Form(True)
):
    if not await check_ui_auth(request): return RedirectResponse("/login")

    # If this is just saving the key, we need to fetch existing settings to preserve them
    if preserve_settings:
        current_config = await get_system_settings()
        providers = current_config.get("providers", {})
        search_limit = current_config.get("search_limit", 5)
        scrape_limit_pages = current_config.get("scrape_limit_pages", 100)
        security = current_config.get("security", {}) # Preserve security settings
        
        # Handle Clearing Keys (Override input if clear is requested)
        if clear_google_books_api_key: google_books_api_key = ""
        if clear_prh_api_key: prh_api_key = ""
        if clear_hardcover_api_key: hardcover_api_key = ""
        if clear_nyt_api_key: nyt_api_key = ""
        if clear_static_api_key: static_api_key = ""


        # Only update the key
        await save_system_settings(providers, search_limit, scrape_limit_pages, google_books_api_key, prh_api_key, hardcover_api_key, static_api_key, security)
        
    else:
        # Main settings form update
        providers = {
            "audible": prov_audible,
            "itunes": prov_itunes,
            "goodreads": prov_goodreads,
            "prh": prov_prh,
            "google": prov_google,
            "hardcover": prov_hardcover
        }
        
        # Parse Security Settings
        security = {
            "enable_country_block": sec_enable_country,
            "allowed_countries": [c.strip() for c in sec_allowed_countries.split(",") if c.strip()],
            "enable_ua_block": sec_enable_ua,
            "required_ua_keywords": [k.strip() for k in sec_required_ua.split(",") if k.strip()],
            "enable_strict_anti_scan": sec_anti_scan
        }
        
        # Don't overwrite key with None if not in this form
        await save_system_settings(providers, limit, scrape_limit, google_books_api_key=google_books_api_key, prh_api_key=prh_api_key, hardcover_api_key=hardcover_api_key, nyt_api_key=nyt_api_key, static_api_key=static_api_key, security=security)

    return RedirectResponse(url="/settings?saved=true", status_code=303)


@router.post("/settings/flush")
async def flush_cache_action(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    await flush_all_cache()
    return RedirectResponse(url="/settings?flushed=true", status_code=303)


@router.get("/logs")
async def view_system_logs(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    return RedirectResponse(url="/settings#logs", status_code=303)

@router.get("/settings/logs/download")
async def download_access_logs(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    async def iter_csv():
        # Header
        yield "Timestamp,Status,Method,Path,IP,Duration(ms),UserAgent\n"
        
        # Stream all logs (newest first)
        cursor = access_logs_collection.find().sort("timestamp", -1)
        async for log in cursor:
            ts = log.get("timestamp", "").isoformat() if log.get("timestamp") else ""
            status = str(log.get("status_code", ""))
            method = log.get("method", "")
            path = f"{log.get('path', '')}?{log.get('query', '')}"
            ip = log.get("ip", "")
            dur = str(log.get("duration_ms", ""))
            ua = log.get("user_agent", "").replace(",", ";") # Simple CSV escape
            
            yield f"{ts},{status},{method},{path},{ip},{dur},{ua}\n"

    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=access_logs.csv"}
    )

# --- BLOCKLIST ROUTES ---

@router.post("/settings/block", response_class=RedirectResponse)
async def block_client(
    request: Request,
    block_value: str = Form(...),
    block_type: str = Form(...),
    reason: str = Form("Manual Ban"),
    user: dict = Depends(get_current_user)
):
    """
    Manually blocks an IP or User-Agent.
    """
    if block_type not in ["ip", "user_agent"]:
        raise HTTPException(status_code=400, detail="Invalid block type")
        
    success = await add_block(block_value, block_type, reason)
    
    return RedirectResponse(url="/settings#blocklist", status_code=303)

@router.post("/settings/unblock", response_class=RedirectResponse)
async def unblock_client(
    request: Request,
    block_value: str = Form(...),
    user: dict = Depends(get_current_user)
):
    """
    Unblocks an IP or User-Agent.
    """
    await remove_block(block_value)
    return RedirectResponse(url="/settings#blocklist", status_code=303)
    await remove_block(block_value)
    return RedirectResponse(url="/settings#blocklist", status_code=303)

# --- BACKUP ROUTES ---

@router.post("/settings/backup/create")
async def create_manual_backup(
    request: Request,
    sections: list = Form(...) # settings, books, logs
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    try:
        await create_backup(sections, trigger="manual")
        return RedirectResponse(url="/settings#backups", status_code=303)
    except Exception as e:
        # TODO: flash error
        print(f"Backup Error: {e}")
        return RedirectResponse(url="/settings?error=backup_failed", status_code=303)

@router.post("/settings/backup/restore")
async def restore_manual_backup(
    request: Request,
    filename: str = Form(...),
    sections: list = Form(None)
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    success = await restore_backup(filename, sections)
    if success:
        await flush_all_cache()
        return RedirectResponse(url="/settings?restored=true#backups", status_code=303)
    else:
        return RedirectResponse(url="/settings?error=restore_failed#backups", status_code=303)

@router.post("/settings/backup/delete")
async def delete_backup_action(
    request: Request,
    filename: str = Form(...)
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    await delete_backup(filename)
    return RedirectResponse(url="/settings#backups", status_code=303)



@router.get("/settings/backup/download/{filename}")
async def download_backup_action(
    request: Request,
    filename: str
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    filepath = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(filepath):
        return FileResponse(filepath, filename=filename, media_type='application/json')
    return RedirectResponse(url="/settings?error=file_not_found#backups", status_code=303)

@router.post("/settings/backup/job/add")
async def add_backup_job(
    request: Request,
    schedule_type: str = Form(...), # daily, weekly
    time: str = Form(...), # HH:MM
    day_of_week: str = Form(None), # sun, mon...
    sections: list = Form(...)
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    job_doc = {
        "schedule_type": schedule_type,
        "time": time,
        "day_of_week": day_of_week,
        "sections": sections,
        "enabled": True,
        "created_at": datetime.datetime.now()
    }
    await backup_jobs_collection.insert_one(job_doc)
    await refresh_scheduler_jobs()
    
    return RedirectResponse(url="/settings#backups", status_code=303)

@router.post("/settings/backup/job/delete")
async def delete_backup_job(
    request: Request,
    job_id: str = Form(...)
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    from bson import ObjectId
    await backup_jobs_collection.delete_one({"_id": ObjectId(job_id)})
    await refresh_scheduler_jobs()
    
    return RedirectResponse(url="/settings#backups", status_code=303)

@router.post("/settings/backup/config")
async def update_backup_config(
    request: Request,
    retention_days: int = Form(0)
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    # Update system settings
    current = await get_system_settings()
    # Merge
    providers = current.get("providers", {})
    limit = current.get("search_limit", 5)
    scrape = current.get("scrape_limit_pages", 100)
    gkey = current.get("google_books_api_key")
    pkey = current.get("prh_api_key")
    hkey = current.get("hardcover_api_key")
    skey = current.get("static_api_key")
    security = current.get("security", {})
    
    # We need to save retention_days. 
    # save_system_settings doesn't support generic args?
    # I need to update save_system_settings?
    # Or just use update_one directly here since it's specific.
    from app.database.core import settings_collection
    await settings_collection.update_one(
        {"_id": "system_settings"},
        {"$set": {"backup_retention_days": retention_days}},
        upsert=True
    )
    
    # Trigger cleanup if enabled?
    if retention_days > 0:
        await cleanup_old_backups(retention_days)

    return RedirectResponse(url="/settings#backups", status_code=303)
