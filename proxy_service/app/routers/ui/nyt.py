from fastapi import APIRouter, Request, Form, HTTPException
from starlette.responses import JSONResponse, RedirectResponse
from app.integrations.nyt import NYTClient
from app.database.lists import save_imported_list
from app.database import upsert_nyt_subscription
from app.routers.ui.utils import check_ui_auth
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/nyt/debug")
async def debug_nyt_connection(request: Request):
    """Debug endpoint to check NYT API connectivity and response structure."""
    if not await check_ui_auth(request): 
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
        
    client = NYTClient()
    api_key = await client._get_api_key()
    
    if not api_key:
        return JSONResponse({"error": "No API Key configured", "status": "failed"})
        
    # Use the same URL as get_list_names
    url = f"https://api.nytimes.com/svc/books/v3/lists/overview.json?api-key={api_key}"
    
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url, timeout=10.0)
            data = response.json()
            
            # Truncate raw response for debug (avoid huge JSON)
            preview = data.copy()
            if "results" in preview and "lists" in preview["results"]:
                # specific truncation for overview.json
                lists = preview["results"]["lists"]
                preview["results"]["lists_summary"] = f"Count: {len(lists)}"
                preview["results"]["lists"] = lists[:1] # Only show first list as sample
            
            return JSONResponse({
                "status_code": response.status_code,
                "url_masked": url.replace(api_key, "HIDDEN"),
                "has_results": "results" in data,
                "list_count": len(data.get("results", {}).get("lists", [])),
                "raw_response_preview": preview
            })
        except Exception as e:
            return JSONResponse({
                "error": str(e),
                "status": "exception",
                "url_masked": url.replace(api_key, "HIDDEN")
            }, status_code=500)

@router.get("/nyt/lists")
async def get_nyt_lists(request: Request):
    """Returns available NYT lists for the modal."""
    if not await check_ui_auth(request): 
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
        
    client = NYTClient()
    lists = await client.get_list_names()
    
    # Filter to reduce payload size and remove massive 'books' arrays
    clean_lists = []
    for l in lists:
        clean_lists.append({
            "display_name": l.get("display_name"),
            "list_name_encoded": l.get("list_name_encoded")
        })
    
    # Sort by display name
    clean_lists.sort(key=lambda x: x.get("display_name", ""))
    
    return JSONResponse(clean_lists)

@router.post("/nyt/import")
async def import_nyt_list(
    request: Request,
    list_name_encoded: str = Form(...),
    display_name: str = Form(...),
    subscribe: bool = Form(False)
):
    """Imports a specific NYT list."""
    if not await check_ui_auth(request): 
        return RedirectResponse("/login")
        
    client = NYTClient()
    books = await client.get_list_details(list_name_encoded)
    
    if not books:
        # TODO: flash error?
        return RedirectResponse(url="/lists?error=Failed to fetch list", status_code=303)
        
    # Format for storage
    # We store raw items because we don't have ASINs yet.
    raw_items = []
    for book in books:
        # NYT API structure: 
        # { "title": "...", "author": "...", "book_image": "...", "description": "...", "primary_isbn13": "...", ... }
        item = {
            "title": book.get("title", "").title(), # Normalize title casing
            "author": book.get("author"),
            "description": book.get("description"),
            "publisher": book.get("publisher"),
            "isbn13": book.get("primary_isbn13"),
            "isbn10": book.get("primary_isbn10"),
            "rank": book.get("rank"),
            "weeks_on_list": book.get("weeks_on_list"),
            "cover": book.get("book_image"),
            # Add fields compatible with list_detail.html's expectation of a book object
            "authors_str": book.get("author"),
            "cover_image": book.get("book_image") or "/static/img/cover_placeholder.jpg",
            "asin": None, # Signal that this is not an Audible book
            "primary_isbn13": book.get("primary_isbn13")
        }
        raw_items.append(item)
        
    await save_imported_list(
        name=f"NYT: {display_name}", 
        url=f"nyt://{list_name_encoded}", 
        asins=[], 
        source="NYT",
        raw_items=raw_items
    )
    
    if subscribe:
        await upsert_nyt_subscription(list_name_encoded, display_name)
    
    return RedirectResponse(url="/lists", status_code=303)
