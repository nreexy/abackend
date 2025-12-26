from typing import Optional # <--- FIXED: Added this import
from urllib.parse import urlencode
from fastapi import APIRouter, Request, Form, Depends, HTTPException, status, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

# Auth Logic
from app.auth import verify_password, create_access_token, get_current_user, ADMIN_USERNAME, get_active_password_hash, get_password_hash
from app.database import (
    set_stored_password_hash, 
    get_dashboard_stats, 
    get_system_settings, 
    save_system_settings, 
    get_detailed_stats, 
    inspect_cache, 
    flush_all_cache, 
    get_traffic_stats,
    get_system_logs,
    get_all_lists,
    get_list_by_id,
    delete_list_by_id,
    get_library_page,
    delete_book_from_library,
    get_book_from_db,
    get_cache,
    set_cache
)
from app.limiter import limiter

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# --- AUTH HELPER ---
async def check_ui_auth(request: Request):
    """
    Helper to verify auth for UI routes. 
    Returns True if authorized, False if not (triggering redirect).
    """
    try:
        await get_current_user(request)
    except HTTPException as e:
        # If system not initialized, we might want to redirect to setup
        # But this function returns False -> redirects to /login
        # We need to handle the "Not Initialized" case in the router calls or here.
        # For now, let's just return False, and logic in /login will handle setup redirect.
        return False
    return True

# --- SETUP ---
@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if await get_active_password_hash():
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request})

@router.post("/setup")
async def setup_action(
    request: Request, 
    password: str = Form(...), 
    confirm_password: str = Form(...)
):
    if await get_active_password_hash():
        return RedirectResponse(url="/login", status_code=303)

    if password != confirm_password:
        return templates.TemplateResponse("setup.html", {"request": request, "error": "Passwords do not match"})
    
    if len(password) < 8:
        return templates.TemplateResponse("setup.html", {"request": request, "error": "Password must be at least 8 characters"})

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
        
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, response: Response, username: str = Form(...), password: str = Form(...)):
    active_hash = await get_active_password_hash()
    
    if not active_hash:
        return RedirectResponse(url="/setup", status_code=303)

    if username == ADMIN_USERNAME and verify_password(password, active_hash):
        # Create Token
        access_token = create_access_token(data={"sub": username})
        # Set Cookie (HttpOnly)
        resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        
        resp.set_cookie(
            key="access_token", 
            value=access_token, 
            httponly=True,   # JavaScript cannot read it
            secure=False,    # Set to True if you are using HTTPS
            samesite="lax",  # Protects against CSRF
            max_age=60*60*24*7 # 7 Days
        )
        return resp
    else:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid Username or Password"})

@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("access_token")
    return resp

# --- PROTECTED UI PAGES ---

@router.get("/dashboard")
async def dashboard(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    stats = await get_dashboard_stats()
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": stats, "active_page": "dashboard"})

@router.get("/library")
async def view_library(
    request: Request, 
    page: int = 1,
    # --- FILTERS ---
    # FIX: Accept string to handle empty form fields ("") from HTML
    min_rating: Optional[str] = None, 
    provider: Optional[str] = None,
    language: Optional[str] = None,
    year: Optional[str] = None
):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    limit = 50
    
    # Prepare Filters
    filters = {}
    
    # 1. Handle Rating (Convert "" to None)
    if min_rating and min_rating.strip():
        try:
            filters["min_rating"] = float(min_rating)
        except ValueError:
            pass # Ignore invalid inputs
            
    # 2. Handle other strings
    if provider and provider.strip(): 
        filters["provider"] = provider
    if language and language.strip(): 
        filters["language"] = language
    if year and year.strip(): 
        filters["year"] = year

    # Fetch Data
    books, total_count = await get_library_page(page=page, limit=limit, filters=filters)
    
    total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
    if total_pages < 1: total_pages = 1
    
    # Construct query string for pagination links (persist filters)
    params_dict = {k: v for k, v in filters.items() if v is not None}
    filter_params = "&" + urlencode(params_dict) if params_dict else ""

    return templates.TemplateResponse("library.html", {
        "request": request, 
        "books": books, 
        "active_page": "library",
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "filters": filters, 
        "filter_params": filter_params
    })

@router.post("/library/delete")
async def delete_book_action(request: Request, asin: str = Form(...)):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    await delete_book_from_library(asin)
    return RedirectResponse(url="/library", status_code=303)

@router.get("/settings")
async def view_settings(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    config = await get_system_settings()
    
    # Retrieve the token from the cookie to show it in the UI
    token = request.cookies.get("access_token", "")
    
    return templates.TemplateResponse("settings.html", {
        "request": request, 
        "config": config, 
        "active_page": "settings",
        "api_token": token # <--- Pass the token here
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
    prov_hardcover: bool = Form(False),
    preserve_settings: bool = Form(False)
):
    if not await check_ui_auth(request): return RedirectResponse("/login")

    # If this is just saving the key, we need to fetch existing settings to preserve them
    if preserve_settings:
        current_config = await get_system_settings()
        providers = current_config.get("providers", {})
        search_limit = current_config.get("search_limit", 5)
        scrape_limit_pages = current_config.get("scrape_limit_pages", 100)
        
        # Only update the key
        await save_system_settings(providers, search_limit, scrape_limit_pages, google_books_api_key, prh_api_key, hardcover_api_key)
        
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
        # Don't overwrite key with None if not in this form
        await save_system_settings(providers, limit, scrape_limit, google_books_api_key=None, prh_api_key=None, hardcover_api_key=None)

    return RedirectResponse(url="/settings?saved=true", status_code=303)

@router.post("/settings/flush")
async def flush_cache_action(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    await flush_all_cache()
    return RedirectResponse(url="/settings?flushed=true", status_code=303)

@router.get("/details")
async def view_details(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    stats = await get_detailed_stats()
    return templates.TemplateResponse("details.html", {"request": request, "stats": stats, "active_page": "details"})

@router.get("/detail_view")
async def view_detail_page(request: Request, asin: Optional[str] = None):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    book = None
    if asin:
        # Try Cache then DB
        cache_key = f"book_v7:{asin}"
        book = await get_cache(cache_key)
        if not book:
            book = await get_book_from_db(asin)
            if book:
                # Cache it for next time
                await set_cache(cache_key, book)
                
        if book:
             # Format for UI (similar to library view)
             book['authors_str'] = ", ".join(book.get("authors", []))
             book['narrators_str'] = ", ".join(book.get("narrators", []))
             book['genres_str'] = ", ".join(book.get("genres", []))
             s = book.get("series", [])
             book['series_str'] = f"{s[0].get('name')} #{s[0].get('sequence')}" if s else "-"
    
    # Serialize for template
    import json
    def json_serial(obj):
        if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    
    # Jinja's |tojson filter fails on datetime objects, so we pre-serialize the object used in debug view
    # But wait, we want to pass the object to template as normal for access {{ book.title }}, 
    # and ONLY serialize for the {{ book | tojson }} part? 
    # Actually, converting the whole thing to a dict with strings is safer for both.
    if book:
        book = json.loads(json.dumps(book, default=json_serial))

    return templates.TemplateResponse("detail_view.html", {
        "request": request, 
        "book": book,
        "active_page": "detail_view"
    })

@router.get("/lists")
async def view_lists(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    lists = await get_all_lists()
    return templates.TemplateResponse("lists.html", {"request": request, "lists": lists, "active_page": "lists"})

@router.get("/lists/{list_id}")
async def view_list_detail(request: Request, list_id: str):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    list_obj = await get_list_by_id(list_id)
    if not list_obj: return RedirectResponse(url="/lists")
    
    books = []
    for asin in list_obj.get('asins', []):
        cached = await get_cache(f"book_v7:{asin}")
        if cached:
            cached['authors_str'] = ", ".join(cached.get("authors", []))
            cached['narrators_str'] = ", ".join(cached.get("narrators", []))
            books.append(cached)
        else:
            books.append({"asin": asin, "title": "Loading...", "authors_str": "-"})
            
    return templates.TemplateResponse("list_detail.html", {"request": request, "list": list_obj, "books": books, "active_page": "lists"})

@router.get("/search_ui")
async def view_search_ui(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    config = await get_system_settings()
    return templates.TemplateResponse("search_ui.html", {
        "request": request, 
        "config": config,
        "active_page": "search"
    })

@router.get("/logs")
async def view_system_logs(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    logs = await get_system_logs(limit=500)
    logs.reverse()
    
    return templates.TemplateResponse("logs.html", {
        "request": request, 
        "logs": logs, 
        "active_page": "logs"
    })

@router.get("/stats")
async def view_traffic_stats(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    from app.database import get_traffic_stats
    data = await get_traffic_stats()
    return templates.TemplateResponse("traffic.html", {
        "request": request, 
        "data": data, 
        "active_page": "stats"
    })


@router.get("/documentation")
async def view_documentation(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    return templates.TemplateResponse("documentation.html", {
        "request": request, 
        "active_page": "documentation"
    })



@router.post("/lists/delete")
async def delete_list_action(request: Request, list_id: str = Form(...)):
    """Action to delete a list"""
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    await delete_list_by_id(list_id)
    
    # Redirect back to the lists page
    return RedirectResponse(url="/lists", status_code=303)