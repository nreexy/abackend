from fastapi import APIRouter, Request, Form
from starlette.responses import RedirectResponse
from typing import Optional
from urllib.parse import urlencode
import json
import datetime

from app.database import (
    get_library_page, 
    delete_book_from_library, 
    get_book_from_db, 
    get_cache, 
    set_cache,
    get_all_lists, 
    get_list_by_id, 
    delete_list_by_id,
    get_system_settings
)
from .utils import templates, check_ui_auth

router = APIRouter()

# --- LIBRARY ---

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

    return templates.TemplateResponse(request, "library.html", {
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
             book['authors_str'] = ", ".join(book.get("authors") or [])
             book['narrators_str'] = ", ".join(book.get("narrators") or [])
             book['genres_str'] = ", ".join(book.get("genres") or [])
             s = book.get("series", [])
             book['series_str'] = f"{s[0].get('name')} #{s[0].get('sequence')}" if s else "-"
    
    # Serialize for template
    def json_serial(obj):
        if isinstance(obj, (datetime.datetime, datetime.date)): return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    
    if book:
        book = json.loads(json.dumps(book, default=json_serial))

    return templates.TemplateResponse(request, "detail_view.html", {
        "book": book,
        "active_page": "detail_view"
    })

# --- LISTS ---

@router.get("/lists")
async def view_lists(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    lists = await get_all_lists()
    return templates.TemplateResponse(request, "lists.html", {"lists": lists, "active_page": "lists"})

@router.get("/lists/{list_id}")
async def view_list_detail(request: Request, list_id: str):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    list_obj = await get_list_by_id(list_id)
    if not list_obj: return RedirectResponse(url="/lists")
    
    books = []
    # 1. Resolve ASINs
    for asin in list_obj.get('asins', []):
        cached = await get_cache(f"book_v7:{asin}")
        if cached:
            cached['authors_str'] = ", ".join(cached.get("authors", []))
            cached['narrators_str'] = ", ".join(cached.get("narrators", []))
            books.append(cached)
        else:
            books.append({"asin": asin, "title": "Loading...", "authors_str": "-"})
            
    # 2. Add Raw Items (e.g. from NYT Import)
    if 'items' in list_obj:
        books.extend(list_obj['items'])
            
    return templates.TemplateResponse(request, "list_detail.html", {"list": list_obj, "books": books, "active_page": "lists"})

@router.post("/lists/delete")
async def delete_list_action(request: Request, list_id: str = Form(...)):
    """Action to delete a list"""
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    await delete_list_by_id(list_id)
    
    # Redirect back to the lists page
    return RedirectResponse(url="/lists", status_code=303)

# --- SEARCH UI ---

@router.get("/search_ui")
async def view_search_ui(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    config = await get_system_settings()
    return templates.TemplateResponse(request, "search_ui.html", {
        "config": config,
        "active_page": "search"
    })
