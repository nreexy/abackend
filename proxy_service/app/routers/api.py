import asyncio
import uuid
import time
import datetime
from typing import Optional, List, Union
import httpx

from fastapi import APIRouter, HTTPException, Query, Depends, Request, BackgroundTasks
from pydantic import BaseModel

# --- IMPORTS ---
from app.database import (
    get_cache, 
    set_cache, 
    save_custom_fields, 
    log_activity, 
    get_custom_fields, 
    log_provider_stats,
    get_system_settings,
    save_imported_list,
    create_custom_list,
    upsert_book_to_db, 
    get_book_from_db,
    get_all_lists,
    get_list_by_id,
    redis_client 
)
from app.services import audible, itunes, goodreads, compiler, prh, google_books, hardcover, unifier
from app.auth import get_current_user
from app.utils import get_device_hash

# Protect all routes
router = APIRouter(dependencies=[Depends(get_current_user)])

# --- MODELS ---
class CustomFieldsRequest(BaseModel):
    asin: str
    fields: dict

class ImportListRequest(BaseModel):
    url: str

class CreateListRequest(BaseModel):
    name: str
    asins: List[str]

class ImportedListResponse(BaseModel):
    name: str
    id: str
    count: int
    source: str
    imported_at: str

class ListItemDefault(BaseModel):
    asin: str
    title: str
    authors: List[str]

class ListItemEnhanced(ListItemDefault):
    genres: List[str] = []
    cover_image: Optional[str] = None
    rating: Optional[float] = None

class ListItemsResponse(BaseModel):
    items: List[Union[ListItemEnhanced, ListItemDefault]]
    total_count: int
    page: int
    total_pages: int

# --- HELPERS ---

async def process_client_info(request: Request):
    """
    Resolves IP to Country and Anonymized Device Hash.
    """
    client_ip = request.client.host if request.client else "Unknown"
    
    if client_ip in ["127.0.0.1", "::1", "Unknown"]:
        return "Localhost", "Local"

    # 1. Check Redis for Country
    geo_key = f"geo_ip:{client_ip}"
    country = await redis_client.get(geo_key)

    # 2. If missing, fetch from API
    if not country:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://ip-api.com/json/{client_ip}", timeout=1.5)
                if resp.status_code == 200:
                    data = resp.json()
                    country = data.get("countryCode", "Unknown")
                else:
                    country = "Unknown"
        except:
            country = "Unknown"
        
        await redis_client.set(geo_key, country, ex=2592000) # Cache 30 days

    # 3. Anonymize IP
    device_hash = get_device_hash(client_ip)
    
    return device_hash, country

def _init_stats(book_data):
    """Injects default stats fields."""
    now = datetime.datetime.utcnow().isoformat()
    if "cached_at" not in book_data: book_data["cached_at"] = now
    if "last_accessed" not in book_data: book_data["last_accessed"] = now
    if "access_count" not in book_data: book_data["access_count"] = 1
    return book_data

# --- HELPER: TRANSFORM TO AUDIOBOOKSHELF FORMAT ---
def transform_to_abs_format(book: dict):
    """
    Maps internal schema to Audiobookshelf JSON schema.
    """
    # 1. Duration: Convert minutes to seconds
    duration = (book.get("runtime_minutes") or 0) * 60
    
    # 2. Publish Year: Extract YYYY from YYYY-MM-DD
    pub_date = book.get("published_date")
    pub_year = pub_date[:4] if pub_date and len(pub_date) >= 4 else None

    # 3. Series mapping (ABS expects list of objects)
    series_mapped = []
    for s in book.get("series", []):
        series_mapped.append({
            "sequence": s.get("sequence"),
            "name": s.get("name")
        })

    # 4. Flatten Authors List -> Single String
    # e.g. ["Author A", "Author B"] -> "Author A, Author B"
    author_string = ", ".join(book.get("authors", []))
    narrator_string = ", ".join(book.get("narrators", []))

    return {
        "id": book.get("asin"), 
        "asin": book.get("asin"),
        "isbn": book.get("asin"),
        "title": book.get("title"),
        "subtitle": book.get("subtitle"),
        
        # --- CHANGED FIELD ---
        "author": author_string,    
        "narrator": narrator_string,
        # ---------------------

        "series": series_mapped,
        "genres": book.get("genres", []),
        "publishedYear": pub_year,
        "publishedDate": pub_date,
        "publisher": book.get("publisher"),
        "description": book.get("description"),
        "language": book.get("language"),
        "explicit": False,
        "abridged": False,
        "cover": book.get("cover_image"),
        "duration": duration,
        
        "provider": book.get("provider"),
        "rating": book.get("rating"),
        "rating_count": book.get("rating_count")
    }

async def benchmark_call(request_id: str, provider_name: str, func, *args, **kwargs):
    start_time = time.time()
    status = "success"
    results = []
    try:
        if asyncio.iscoroutinefunction(func):
            results = await func(*args, **kwargs)
        else:
            results = await asyncio.to_thread(func, *args, **kwargs)
    except Exception as e:
        status = "error"
        print(f"❌ Error in {provider_name}: {e}")
    finally:
        duration = (time.time() - start_time) * 1000
        count = 0
        if isinstance(results, list): count = len(results)
        elif isinstance(results, tuple): count = len(results[1]) if len(results) > 1 else 0
        elif results: count = 1
        
        await log_provider_stats(request_id, provider_name, round(duration, 2), result_count=count, status=status)
    return results

# --- PING ENDPOINT ---
@router.get("/ping")
async def ping():
    """Simple connectivity check."""
    return {"success": True}

# --- 1. SEARCH ENDPOINT ---
@router.get("/library/search")
async def search_local_library(q: str = Query(..., min_length=2)):
    """
    Search local library for autocomplete.
    """
    from app.database import search_library_books
    results = await search_library_books(q, limit=10)
    
    # Simplify for autocomplete
    simple = []
    for b in results:
        simple.append({
            "asin": b.get("asin"),
            "title": b.get("title"),
            "author": ", ".join(b.get("authors", [])),
            "cover": b.get("cover_image")
        })
    return simple

@router.get("/search")
async def search_audiobook(
    request: Request,
    q: Optional[str] = None,
    author: Optional[str] = None,
    isbn: Optional[str] = None,
    providers: Optional[str] = Query(None),
    min_rating: Optional[float] = Query(None)
):
    start_ts = time.time()
    device_id, country = await process_client_info(request)

    if not q and not author and not isbn:
        raise HTTPException(status_code=400, detail="Must provide 'q', 'author', or 'isbn'")

    config = await get_system_settings()
    active_providers = config.get("providers", {})
    limit = config.get("search_limit", 5)

    cache_str = f"q={q}:auth={author}:isbn={isbn}:prov={providers}:rate={min_rating}:lim={limit}"
    clean_key = cache_str.lower().replace(" ", "").replace(",", "_")
    cache_key = f"search_v14:{clean_key}"
    
    if cached := await get_cache(cache_key):
        duration = (time.time() - start_ts) * 1000
        await log_activity("search", cache_str, details="Cache Hit", device_id=device_id, country=country, duration_ms=duration)
        return [transform_to_abs_format(b) for b in cached]

    try:
        req_id = str(uuid.uuid4())
        tasks = []
        
        if providers:
            target_list = [p.lower().strip() for p in providers.split(",")]
            use_audible = "audible" in target_list
            use_itunes = "itunes" in target_list
            use_goodreads = "goodreads" in target_list
            use_prh = "prh" in target_list
            use_google = "google" in target_list
            use_hardcover = "hardcover" in target_list
        else:
            use_audible = active_providers.get("audible", True)
            use_itunes = active_providers.get("itunes", True)
            use_goodreads = active_providers.get("goodreads", True)
            use_prh = active_providers.get("prh", True)
            use_google = active_providers.get("google", False)
            use_hardcover = active_providers.get("hardcover", False)

        if use_audible:
            async def run_audible():
                raw = await asyncio.to_thread(audible.search_raw, query=q, author=author, isbn=isbn, limit=limit)
                if raw:
                    sub = [compiler.compile_audible_metadata(p['asin'], p) for p in raw]
                    return await asyncio.gather(*sub)
                return []
            tasks.append(benchmark_call(req_id, "Audible", run_audible))

        if use_itunes:
            tasks.append(benchmark_call(req_id, "iTunes", itunes.search_raw, query=q, author=author, isbn=isbn, limit=limit))

        if use_goodreads:
            search_term = isbn if isbn else (f"{q} {author}" if q and author else (q or author))
            tasks.append(benchmark_call(req_id, "Goodreads", goodreads.search_scraper, search_term))

        if use_prh:
                    if isbn:
                        # FIX: If we have an ISBN, use fetch_details (direct lookup)
                        # instead of search_raw (text search), which often fails for numbers.
                        async def prh_isbn_lookup():
                            # Ensure clean ISBN (PRH dislikes hyphens in URL path)
                            clean_isbn = isbn.replace("-", "").strip()
                            detail = await prh.fetch_details(clean_isbn)
                            return [detail] if detail else []

                        tasks.append(benchmark_call(req_id, "PRH", prh_isbn_lookup))
                    else:
                        # Standard text search
                        search_term = q or author
                        if search_term:
                            tasks.append(benchmark_call(req_id, "PRH", prh.search_raw, search_term, limit=limit))
        if use_google:
            search_term = isbn if isbn else (f"{q} {author}" if q and author else (q or author))
            api_key = config.get("google_books_api_key")
            if api_key:
                tasks.append(benchmark_call(req_id, "Google Books", google_books.search_book, search_term, api_key, limit=limit))
            else:
                print("⚠️ Google Books enabled but no API Key found.")
        
        if use_hardcover:
            search_term = q or author
            api_key = config.get("hardcover_api_key")
            if api_key:
                tasks.append(benchmark_call(req_id, "Hardcover", hardcover.search_book, search_term, api_key, limit=limit))
            else:
                print("⚠️ Hardcover enabled but no API Key found.")

        results_list = await asyncio.gather(*tasks)
        
        full_results = []
        seen_ids = set()
        for provider_results in results_list:
            if provider_results: full_results.extend(provider_results)

        filtered_results = []
        for book in full_results:
            if min_rating:
                r = book.get("rating")
                if r is None or r < min_rating: continue
            if book['asin'] in seen_ids: continue
            seen_ids.add(book['asin'])
            
            book = _init_stats(book)
            filtered_results.append(book)

        # Cache & Persist
        for book in filtered_results:
            if book and "asin" in book:
                await upsert_book_to_db(book)
                await set_cache(f"book_v7:{book['asin']}", book)

        await set_cache(cache_key, filtered_results)
        
        duration = (time.time() - start_ts) * 1000
        await log_activity("search", cache_str, details="Multi-Provider Query", device_id=device_id, country=country, duration_ms=duration)
        
        # Return ABS format
        # UNIFIED PATH:
        unified_results = await unifier.unify_search_results([filtered_results])
        return [transform_to_abs_format(b) for b in unified_results]

    except Exception as e:
        print(f"❌ Global Search Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 1b. PRH EXTENSIONS ---
@router.get("/prh/also-purchased/{isbn}")
async def get_prh_recommendations(isbn: str):
    """
    Get 'Also Purchased' recommendations from PRH.
    """
    results = await prh.get_recommendations(isbn) 
    return results

# --- 2. DETAILS ENDPOINT ---
@router.get("/book/{asin}")
async def get_book_details(asin: str, request: Request):
    start_ts = time.time()
    device_id, country = await process_client_info(request)
    cache_key = f"book_v7:{asin}"
    
    def get_dur(): return (time.time() - start_ts) * 1000

    # 1. Cache Check
    if cached := await get_cache(cache_key):
        cached["access_count"] = cached.get("access_count", 0) + 1
        cached["last_accessed"] = datetime.datetime.utcnow().isoformat()
        await set_cache(cache_key, cached)
        cached["custom_metadata"] = await get_custom_fields(asin) or {}
        await log_activity("fetch_metadata", asin, details="Redis Hit", device_id=device_id, country=country, duration_ms=get_dur())
        return cached

    # 2. DB Check
    if stored := await get_book_from_db(asin):
        await set_cache(cache_key, stored)
        stored["custom_metadata"] = await get_custom_fields(asin) or {}
        await log_activity("fetch_metadata", asin, details="Mongo Hit", device_id=device_id, country=country, duration_ms=get_dur())
        return stored

    async def finalize(data, source):
        data = _init_stats(data)
        data["custom_metadata"] = await get_custom_fields(asin) or {}
        await upsert_book_to_db(data)
        await set_cache(cache_key, data)
        await log_activity("fetch_metadata", asin, details=source, device_id=device_id, country=country, duration_ms=get_dur())
        return data

    try:
        raw = await asyncio.to_thread(audible.get_product_raw, asin)
        data = await compiler.compile_audible_metadata(asin, raw)
        return await finalize(data, "Audible")
    except: pass

    if data := await itunes.fetch_details(asin):
        return await finalize(data, "iTunes")
            
    if asin.isdigit() and len(asin) == 13:
        if data := await prh.fetch_details(asin):
            return await finalize(data, "PRH")

    await log_activity("fetch_error", asin, details="Not found", device_id=device_id, country=country, duration_ms=get_dur())
    raise HTTPException(status_code=404, detail="Book not found")

# --- 3. LIST IMPORT ENDPOINT ---
# --- REFACTORED IMPORT LOGIC ---
async def execute_list_import(url: str, device_id: str, country: str, req_id: str):
    """
    Core logic for importing a list. Can be run synchronously or in background.
    """
    start_ts = time.time()
    config = await get_system_settings()
    max_pages = config.get("scrape_limit_pages", 100)

    # --- A: GOODREADS ---
    if "goodreads.com" in url:
        try:
            list_title, books = await benchmark_call(
                req_id, "Goodreads Scraper", goodreads.scrape_list_from_url, url, max_pages=max_pages
            )
        except Exception as e:
            print(f"❌ Goodreads Import Error: {e}")
            # If running in background, we can't raise HTTPException to user.
            # But for sync calls, we might want to propagate.
            # For now, we'll re-raise if it's a critical logic error, or just log.
            raise e

        if not books: raise Exception("No books found.")

        for book in books:
            book = _init_stats(book)
            await upsert_book_to_db(book)
            await set_cache(f"book_v7:{book['asin']}", book)

        asins = [b['asin'] for b in books]
        await save_imported_list(list_title, url, asins, source="Goodreads")
        
        duration = (time.time() - start_ts) * 1000
        await log_activity("import_list", list_title, details=f"GR: {len(books)} items", device_id=device_id, country=country, duration_ms=duration)
        return {"status": "success", "title": list_title, "count": len(books)}

    # --- B: AUDIBLE ---
    else:
        try:
            list_title, asins = await benchmark_call(
                req_id, "Audible Scraper", asyncio.to_thread, audible.scrape_list_from_url, url
            )
        except Exception as e:
            print(f"❌ Audible Import Error: {e}")
            raise e
        
        if not asins: raise Exception("No ASINs found.")
        
        successful_books = []

        async def fetch_and_persist_book(asin):
            cache_key = f"book_v7:{asin}"
            if await get_cache(cache_key): return True
            if await get_book_from_db(asin): return True

            try:
                async def _get_data():
                    raw = await asyncio.to_thread(audible.get_product_raw, asin)
                    return await compiler.compile_audible_metadata(asin, raw)

                meta = await benchmark_call(req_id, "Audible", _get_data)
                if meta:
                    meta = _init_stats(meta)
                    await upsert_book_to_db(meta)
                    await set_cache(cache_key, meta)
                    return True
            except:
                await log_provider_stats(req_id, "Audible", 0, 0, "error")
            return False

        chunk_size = 10
        for i in range(0, len(asins), chunk_size):
            chunk = asins[i:i + chunk_size]
            tasks = [fetch_and_persist_book(asin) for asin in chunk]
            results = await asyncio.gather(*tasks)
            for res in results:
                if res: successful_books.append(1)
            await asyncio.sleep(0.2)

        await save_imported_list(list_title, url, asins, source="Audible")
        
        duration = (time.time() - start_ts) * 1000
        await log_activity("import_list", list_title, details=f"Items: {len(successful_books)}/{len(asins)}", device_id=device_id, country=country, duration_ms=duration)
        
        return {"status": "success", "title": list_title, "count": len(asins), "imported": len(successful_books)}

# --- 3. LIST IMPORT ENDPOINT (SYNC) ---
@router.post("/lists/import")
async def import_audible_list(data: ImportListRequest, request: Request):
    device_id, country = await process_client_info(request)
    req_id = str(uuid.uuid4())
    
    try:
        return await execute_list_import(data.url, device_id, country, req_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Import failed: {str(e)}")

# --- 3b. LIST IMPORT ENDPOINT (ASYNC) ---
@router.post("/lists/import/async")
async def import_audible_list_async(data: ImportListRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Starts the import process in the background.
    Returns immediately if the URL looks valid.
    """
    # 1. Basic Validation
    if "audible.com" not in data.url and "goodreads.com" not in data.url:
         raise HTTPException(status_code=400, detail="Unsupported URL. Must be Audible or Goodreads.")

    # 2. Prepare Context
    device_id, country = await process_client_info(request)
    req_id = str(uuid.uuid4())

    # 3. Wrapper to handle background exceptions safely
    async def safe_import_task(url, dev_id, ctry, rid):
        try:
            await execute_list_import(url, dev_id, ctry, rid)
        except Exception as e:
            print(f"❌ Background Import Failed for {url}: {e}")
            await log_activity("import_error", url, details=str(e), device_id=dev_id, country=ctry)

    # 4. Enqueue Task
    background_tasks.add_task(safe_import_task, data.url, device_id, country, req_id)

    return {"status": "accepted", "message": "Import started in background", "request_id": req_id}

@router.get("/lists/imported", response_model=List[ImportedListResponse])
async def get_imported_lists(request: Request):
    """
    Returns a list of all imported lists (excluding custom lists).
    """
    # 1. Fetch all lists
    all_lists = await get_all_lists()
    
    # 2. Filter and Format
    response = []
    for lst in all_lists:
        # Filter: Only include 'imported' type
        if lst.get("type") != "imported":
            continue
            
        # Format Date
        created_at = lst.get("created_at")
        if isinstance(created_at, datetime.datetime):
            date_str = created_at.strftime("%Y-%m-%d")
        else:
            date_str = str(created_at)[:10]

        response.append(ImportedListResponse(
            name=lst.get("name", "Unknown List"),
            id=str(lst.get("_id")),
            count=lst.get("count", 0),
            source=lst.get("source", "Unknown"),
            imported_at=date_str
        ))
        
    return response

@router.get("/lists/{list_id}/items", response_model=ListItemsResponse)
async def get_list_items(
    list_id: str, 
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=100),
    enhanced: bool = Query(False)
):
    """
    Retrieve items from a specific list with pagination and detail levels.
    """
    # 1. Fetch List
    list_obj = await get_list_by_id(list_id)
    if not list_obj:
        raise HTTPException(status_code=404, detail="List not found")
        
    all_asins = list_obj.get("asins", [])
    total_count = len(all_asins)
    total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
    
    # 2. Pagination
    start = (page - 1) * limit
    end = start + limit
    page_asins = all_asins[start:end]
    
    # 3. Fetch Metadata
    items = []
    req_id = str(uuid.uuid4()) # For internal logging if needed
    
    async def fetch_item(asin):
        # Try Cache/DB first
        cache_key = f"book_v7:{asin}"
        if cached := await get_cache(cache_key): return cached
        if stored := await get_book_from_db(asin): 
             await set_cache(cache_key, stored)
             return stored
             
        # If not found, try to fetch live (optional, might be slow for lists)
        # For now, we'll just return basic info if missing
        return {"asin": asin, "title": "Unknown Title", "authors": []}

    # Fetch in parallel
    tasks = [fetch_item(asin) for asin in page_asins]
    results = await asyncio.gather(*tasks)
    
    # 4. Map to Model
    for data in results:
        base_info = {
            "asin": data.get("asin", "Unknown"),
            "title": data.get("title", "Unknown"),
            "authors": data.get("authors", [])
        }
        
        if enhanced:
            items.append(ListItemEnhanced(
                **base_info,
                genres=data.get("genres", []),
                cover_image=data.get("cover_image"),
                rating=data.get("rating")
            ))
        else:
            items.append(ListItemDefault(**base_info))
            
    return ListItemsResponse(
        items=items,
        total_count=total_count,
        page=page,
        total_pages=total_pages
    )

# --- 4. CREATE MANUAL LIST ---
@router.post("/lists/create")
async def create_manual_list(data: CreateListRequest, request: Request):
    start_ts = time.time()
    device_id, country = await process_client_info(request)
    req_id = str(uuid.uuid4())
    
    clean_asins = list(set([a.strip() for a in data.asins if a and a.strip()]))
    if not clean_asins: raise HTTPException(status_code=400, detail="No valid ASINs provided")

    await create_custom_list(data.name, clean_asins)
    
    successful_count = 0

    async def fetch_and_persist_book(asin):
        cache_key = f"book_v7:{asin}"
        if await get_cache(cache_key): return True
        if await get_book_from_db(asin): return True
        
        try:
            async def _get_data():
                raw = await asyncio.to_thread(audible.get_product_raw, asin)
                return await compiler.compile_audible_metadata(asin, raw)
            
            meta = await benchmark_call(req_id, "Audible", _get_data)
            if meta: 
                meta = _init_stats(meta)
                await upsert_book_to_db(meta)
                await set_cache(cache_key, meta)
                return True
        except: pass
        return False

    for i in range(0, len(clean_asins), 10):
        chunk = clean_asins[i:i + 10]
        results = await asyncio.gather(*[fetch_and_persist_book(a) for a in chunk])
        for res in results:
            if res: successful_count += 1
        await asyncio.sleep(0.1)

    duration = (time.time() - start_ts) * 1000
    await log_activity("create_list", data.name, details=f"Items: {successful_count}", device_id=device_id, country=country, duration_ms=duration)
    return {"status": "success", "name": data.name, "count": len(clean_asins)}

# --- 5. CUSTOM FIELDS ---
@router.post("/custom-fields")
async def add_custom_fields(data: CustomFieldsRequest):
    await save_custom_fields(data.asin, data.fields)
    return {"status": "success", "asin": data.asin}