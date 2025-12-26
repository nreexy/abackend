import httpx
import html
import json
import logging
import asyncio
from app.database import get_system_settings
from app.utils import normalize_language

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Base URL for the Enhanced API (V2)
BASE_URL = "https://api.penguinrandomhouse.com/resources/v2/title/domains/PRH.US"

async def search_raw(query: str, limit: int = 5):
    """
    Performs a 'Work-First' search:
    1. Queries the /search endpoint to find Book Works.
    2. Resolves those Works to specific Audiobook Titles.
    """
    url = f"{BASE_URL}/search"
    
    config = await get_system_settings()
    api_key = config.get("prh_api_key")
    
    if not api_key:
        logger.warning("⚠️ PRH enabled but no API Key found.")
        return []

    # Ask for docType=work to find the abstract book entity
    params = {
        "api_key": api_key,
        "q": query,
        "rows": limit + 3,
        "docType": "work",
        "sort": "score"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    logger.info(f"🚀 PRH SEARCH: {query}")
    
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(url, params=params, headers=headers, timeout=15.0)
            
            work_ids = []
            if resp.status_code == 200:
                data = resp.json()
                root = data.get("data", data)
                results = root.get("results", [])
                
                # Extract IDs for items that are Works
                for item in results:
                    if item.get("docType") == "work":
                        if work_id := item.get("key"):
                            work_ids.append(work_id)
            
            if not work_ids:
                return []

            # Resolve Works to Audiobooks
            target_ids = work_ids[:limit]
            tasks = [fetch_work_audiobooks(client, wid, api_key) for wid in target_ids]
            results_nested = await asyncio.gather(*tasks)
            
            # Flatten and Dedupe
            final_audiobooks = []
            seen_isbns = set()
            for work_results in results_nested:
                for book in work_results:
                    if book['asin'] not in seen_isbns:
                        final_audiobooks.append(book)
                        seen_isbns.add(book['asin'])
                
            logger.info(f"   ✅ PRH Search: Found {len(final_audiobooks)} Audiobooks.")
            return final_audiobooks

    except Exception as e:
        logger.error(f"   ⚠️ PRH Search Exception: {e}")
    
    return []

async def get_recommendations(isbn: str, limit: int = 4):
    """
    Fetch 'Also Purchased' recommendations.
    Parses 'Works' and resolves them to Audiobooks.
    """
    # Use the view endpoint provided in the example
    url = f"{BASE_URL}/titles/{isbn}/views/also-purchased"
    
    config = await get_system_settings()
    api_key = config.get("prh_api_key")
    
    if not api_key:
        return []

    params = {
        "api_key": api_key,
        "rows": limit + 2,
        "format": "json"
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10.0)
            
            if resp.status_code == 200:
                data = resp.json()
                root = data.get("data", data)
                
                # The endpoint returns a list of 'works'
                works = root.get("works", [])
                
                # Filter: Only works that claim to have an Audio Edition
                audio_works = [w for w in works if w.get("hasAudioEdition") is True]
                
                # Extract Work IDs
                work_ids = [w.get("workId") for w in audio_works][:limit]
                
                if not work_ids:
                    return []

                # Resolve Work IDs to actual Audiobook Titles
                tasks = [fetch_work_audiobooks(client, wid, api_key) for wid in work_ids]
                results_nested = await asyncio.gather(*tasks)
                
                # Flatten results
                recs = []
                seen = set()
                for group in results_nested:
                    for book in group:
                        if book['asin'] not in seen:
                            recs.append(book)
                            seen.add(book['asin'])
                
                return recs[:limit]
                
    except Exception as e:
        logger.error(f"   ⚠️ PRH Recommendations Error: {e}")
        
    return []

async def fetch_work_audiobooks(client, work_id, api_key):
    """
    Helper: Fetch all titles for a Work ID and return only Audiobooks.
    """
    url = f"{BASE_URL}/works/{work_id}/titles"
    params = {"api_key": api_key}
    
    try:
        resp = await client.get(url, params=params, timeout=8.0)
        if resp.status_code == 200:
            data = resp.json()
            
            titles = []
            if "data" in data and "titles" in data["data"]:
                titles = data["data"]["titles"]
            elif "titles" in data:
                titles = data["titles"]
            
            return [format_prh_result(t) for t in titles if is_audiobook(t)]
            
    except Exception as e:
        pass
        
    return []

async def fetch_details(isbn: str):
    """
    Lookup specific ISBN using the direct /titles/{isbn} endpoint.
    """
    url = f"{BASE_URL}/titles/{isbn}"
    
    config = await get_system_settings()
    api_key = config.get("prh_api_key")
    if not api_key: return None

    params = {"api_key": api_key}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10.0)
            
            if resp.status_code == 200:
                data = resp.json()
                if "data" in data and "titles" in data["data"]:
                    items = data["data"]["titles"]
                    if items: return format_prh_result(items[0])
                elif "data" in data and "isbn" in data["data"]:
                     return format_prh_result(data["data"])
    except: pass
    return None

def is_audiobook(item):
    """Helper to check if item is an audiobook"""
    fmt = item.get("format", {})
    code = fmt.get("code") if isinstance(fmt, dict) else item.get("formatCode")
    desc = fmt.get("description", "").lower() if isinstance(fmt, dict) else item.get("formatName", "").lower()
    
    if not code: code = item.get("formatCode")
    
    if code in ["DN", "CD", "AD", "AJ"]: return True
    if "audio" in desc: return True
    return False

def format_prh_result(item):
    """Map PRH JSON to Standard Schema"""
    isbn = str(item.get("isbn", ""))
    title = item.get("title", "") or item.get("titleweb", "")
    subtitle = item.get("subtitle")

    # Author Parsing (Handles strings and list of dicts)
    authors = []
    author_field = item.get("author") or item.get("authorweb")
    if author_field:
        if isinstance(author_field, list):
            for a in author_field:
                if isinstance(a, dict):
                    if val := a.get("authorDisplay"): authors.append(val)
                else:
                    authors.append(str(a))
        else:
            authors = [str(author_field)]
    
    # Description
    desc_html = item.get("flapcopy") or item.get("description") or ""
    description = html.unescape(desc_html)
    
    # Publisher
    publisher = ""
    imprint_data = item.get("imprint")
    if isinstance(imprint_data, dict):
        publisher = imprint_data.get("description")
    elif isinstance(imprint_data, str):
        publisher = imprint_data

    # Series
    series_list = []
    if item.get("series"):
        s_val = item.get("series")
        if isinstance(s_val, str):
             series_list.append({"name": s_val, "sequence": str(item.get("seriesNumber", ""))})
        elif isinstance(s_val, dict):
             series_list.append({"name": s_val.get("name"), "sequence": str(item.get("seriesNumber", ""))})

    # Genres (Parsed from 'subjects')
    genres = []
    subjects = item.get("subjects")
    if subjects and isinstance(subjects, list):
        for subj in subjects:
            if isinstance(subj, dict):
                g_name = subj.get("description")
                if g_name:
                    genres.append(g_name)

    # Cover & Runtime
    cover = f"https://images.randomhouse.com/cover/{isbn}"
    runtime = 0 
    try:
        if item.get("projectedMinutes"):
            runtime = int(item.get("projectedMinutes"))
        elif item.get("pages"):
            runtime = int(item.get("pages"))
    except: pass
    
    pub_date = item.get("onsale") or item.get("onsaledate")
    if pub_date: pub_date = pub_date[:10]

    return {
        "asin": isbn, 
        "title": title,
        "subtitle": subtitle,
        "authors": authors,
        "narrators": [],
        "series": series_list,
        "publisher": publisher,
        "published_date": pub_date,
        "language": normalize_language("en"), 
        "genres": genres, 
        "description": description,
        "rating": None, 
        "rating_count": 0,
        "runtime_minutes": runtime,
        "cover_image": cover,
        "sample_url": None,
        "chapters": [],
        "custom_metadata": {
            "format": item.get("format", {}).get("description"),
            "formatCode": item.get("format", {}).get("code")
        },
        "provider": "Penguin Random House"
    }