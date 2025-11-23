import asyncio
import uuid
import time
import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel

# Imports from your app structure
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
    get_book_from_db
)
from app.services import audible, itunes, goodreads, compiler, prh
from app.auth import get_current_user

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

# --- HELPER: STATS INITIALIZER ---
def _init_stats(book_data):
    now = datetime.datetime.utcnow().isoformat()
    if "cached_at" not in book_data: book_data["cached_at"] = now
    if "last_accessed" not in book_data: book_data["last_accessed"] = now
    if "access_count" not in book_data: book_data["access_count"] = 1
    return book_data

# --- HELPER: BENCHMARKING ---
async def benchmark_call(request_id: str, provider_name: str, func, *args, **kwargs):
    start_time = time.time()
    status = "success"
    results = []
    try:
        # Check if the function is a coroutine (async) or a regular function
        if asyncio.iscoroutinefunction(func):
            results = await func(*args, **kwargs)
        else:
            # If it's a sync function (like Audible), run it in a separate thread
            results = await asyncio.to_thread(func, *args, **kwargs)
    except Exception as e:
        status = "error"
        print(f"❌ Error in {provider_name}: {e}")
    finally:
        duration = (time.time() - start_time) * 1000
        if isinstance(results, list): count = len(results)
        elif isinstance(results, tuple): count = len(results[1]) if len(results) > 1 else 0
        elif results: count = 1
        else: count = 0
        
        await log_provider_stats(request_id, provider_name, round(duration, 2), result_count=count, status=status)
    return results

# --- 1. SEARCH ENDPOINT ---
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
    client_ip = request.client.host if request.client else "Unknown"

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
        await log_activity("search", cache_str, details="Cache Hit", ip=client_ip, duration_ms=duration)
        return cached

    try:
        req_id = str(uuid.uuid4())
        tasks = []
        
        if providers:
            target_list = [p.lower().strip() for p in providers.split(",")]
            use_audible = "audible" in target_list
            use_itunes = "itunes" in target_list
            use_goodreads = "goodreads" in target_list
            use_prh = "prh" in target_list
        else:
            use_audible = active_providers.get("audible", True)
            use_itunes = active_providers.get("itunes", True)
            use_goodreads = active_providers.get("goodreads", True)
            use_prh = active_providers.get("prh", True)

        # --- TASK DEFINITIONS ---
        
        # AUDIBLE (Sync Library -> Runs in Thread)
        if use_audible:
            async def run_audible():
                # Offload the blocking audible.search_raw to a thread
                raw = await asyncio.to_thread(audible.search_raw, query=q, author=author, isbn=isbn, limit=limit)
                if raw:
                    sub = [compiler.compile_audible_metadata(p['asin'], p) for p in raw]
                    return await asyncio.gather(*sub)
                return []
            tasks.append(benchmark_call(req_id, "Audible", run_audible))

        # ITUNES (Async -> Runs on Event Loop)
        if use_itunes:
            tasks.append(benchmark_call(req_id, "iTunes", itunes.search_raw, query=q, author=author, isbn=isbn, limit=limit))

        # GOODREADS (Async -> Runs on Event Loop)
        if use_goodreads:
            search_term = isbn if isbn else (f"{q} {author}" if q and author else (q or author))
            tasks.append(benchmark_call(req_id, "Goodreads", goodreads.search_scraper, search_term))

        # PRH (Async -> Runs on Event Loop)
        if use_prh:
            search_term = isbn if isbn else (q or author)
            tasks.append(benchmark_call(req_id, "PRH", prh.search_raw, search_term, limit=limit))
        
        # --- PARALLEL EXECUTION ---
        # asyncio.gather runs all tasks (Threaded and Async) simultaneously
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
        await log_activity("search", cache_str, details="Multi-Provider Query", ip=client_ip, duration_ms=duration)
        return filtered_results

    except Exception as e:
        print(f"❌ Global Search Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 2. DETAILS ENDPOINT ---
@router.get("/book/{asin}")
async def get_book_details(asin: str, request: Request):
    start_ts = time.time()
    client_ip = request.client.host if request.client else "Unknown"
    cache_key = f"book_v7:{asin}"
    
    def get_dur(): return (time.time() - start_ts) * 1000

    # 1. Cache Check
    if cached := await get_cache(cache_key):
        cached["access_count"] = cached.get("access_count", 0) + 1
        cached["last_accessed"] = datetime.datetime.utcnow().isoformat()
        await set_cache(cache_key, cached)
        cached["custom_metadata"] = await get_custom_fields(asin) or {}
        await log_activity("fetch_metadata", asin, details="Redis Hit", ip=client_ip, duration_ms=get_dur())
        return cached

    # 2. DB Check
    if stored := await get_book_from_db(asin):
        await set_cache(cache_key, stored)
        stored["custom_metadata"] = await get_custom_fields(asin) or {}
        await log_activity("fetch_metadata", asin, details="Mongo Hit", ip=client_ip, duration_ms=get_dur())
        return stored

    async def finalize(data, source):
        data = _init_stats(data)
        data["custom_metadata"] = await get_custom_fields(asin) or {}
        await upsert_book_to_db(data)
        await set_cache(cache_key, data)
        await log_activity("fetch_metadata", asin, details=source, ip=client_ip, duration_ms=get_dur())
        return data

    # 3. Provider Fetching
    try:
        # Thread the blocking Audible call
        raw = await asyncio.to_thread(audible.get_product_raw, asin)
        data = await compiler.compile_audible_metadata(asin, raw)
        return await finalize(data, "Audible")
    except: pass

    if data := await itunes.fetch_details(asin):
        return await finalize(data, "iTunes")
            
    if asin.isdigit() and len(asin) == 13:
        if data := await prh.fetch_details(asin):
            return await finalize(data, "PRH")

    await log_activity("fetch_error", asin, details="Not found", ip=client_ip, duration_ms=get_dur())
    raise HTTPException(status_code=404, detail="Book not found")

# --- 3. LIST IMPORT ENDPOINT ---
@router.post("/lists/import")
async def import_audible_list(data: ImportListRequest, request: Request):
    url = data.url
    req_id = str(uuid.uuid4())
    client_ip = request.client.host if request.client else "Unknown"
    start_ts = time.time()

    # 1. FETCH SETTINGS (For Goodreads Limit)
    config = await get_system_settings()
    max_pages = config.get("scrape_limit_pages", 100)

    # --- BRANCH A: GOODREADS IMPORT ---
    if "goodreads.com" in url:
        try:
            # Scrape List (Returns full book objects, not just IDs)
            # We pass 'max_pages' to control how deep we scrape
            list_title, books = await benchmark_call(
                req_id, 
                "Goodreads Scraper", 
                goodreads.scrape_list_from_url, 
                url, 
                max_pages=max_pages 
            )

        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Goodreads scrape failed: {str(e)}")

        if not books:
            raise HTTPException(status_code=400, detail="No books found.")

        # Save Books to Library & Cache
        # Goodreads scraper already returns full metadata, so we just save it.
        for book in books:
            book = _init_stats(book) # Add 'added_at', 'access_count'
            await upsert_book_to_db(book)
            await set_cache(f"book_v7:{book['asin']}", book)

        # Save List Collection
        asins = [b['asin'] for b in books]
        await save_imported_list(list_title, url, asins, source="Goodreads")
        
        duration = (time.time() - start_ts) * 1000
        await log_activity("import_list", list_title, details=f"GR: {len(books)} items", ip=client_ip, duration_ms=duration)
        
        return {"status": "success", "title": list_title, "count": len(books)}

    # --- BRANCH B: AUDIBLE IMPORT ---
    else:
        try:
            # Scrape ASINs only
            list_title, asins = await benchmark_call(
                req_id, 
                "Audible Scraper", 
                audible.scrape_list_from_url, 
                url
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Scrape failed: {str(e)}")
        
        if not asins: 
            raise HTTPException(status_code=400, detail="No ASINs found at that URL.")
        
        successful_books = []

        # Helper to fetch individual book metadata via API
        async def fetch_and_persist_book(asin):
            cache_key = f"book_v7:{asin}"
            
            # Skip if we already have it
            if await get_cache(cache_key): return True
            if await get_book_from_db(asin): return True

            try:
                # Thread the blocking API call
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
                # Log failure but don't stop the whole import
                await log_provider_stats(req_id, "Audible", 0, 0, "error")
            
            return False

        # Process in chunks to be polite to the API
        chunk_size = 10
        for i in range(0, len(asins), chunk_size):
            chunk = asins[i:i + chunk_size]
            tasks = [fetch_and_persist_book(asin) for asin in chunk]
            results = await asyncio.gather(*tasks)
            
            # Count successes
            for res in results:
                if res: successful_books.append(1)
                
            await asyncio.sleep(0.2)

        # Save List Collection
        await save_imported_list(list_title, url, asins, source="Audible")
        
        duration = (time.time() - start_ts) * 1000
        await log_activity("import_list", list_title, details=f"Items: {len(successful_books)}/{len(asins)}", ip=client_ip, duration_ms=duration)
        
        return {
            "status": "success", 
            "title": list_title, 
            "count": len(asins),
            "imported": len(successful_books)
        }

# --- 4. CREATE MANUAL LIST ---
@router.post("/lists/create")
async def create_manual_list(data: CreateListRequest, request: Request):
    start_ts = time.time()
    client_ip = request.client.host if request.client else "Unknown"
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
    await log_activity("create_list", data.name, details=f"Items: {successful_count}", ip=client_ip, duration_ms=duration)
    return {"status": "success", "name": data.name, "count": len(clean_asins)}

# --- 5. CUSTOM FIELDS ---
@router.post("/custom-fields")
async def add_custom_fields(data: CustomFieldsRequest):
    await save_custom_fields(data.asin, data.fields)
    return {"status": "success", "asin": data.asin}