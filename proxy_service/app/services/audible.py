# app/services/audible.py
import os
import audible
from fastapi import HTTPException
from app.config import AUDIBLE_AUTH_FILE, RESPONSE_GROUPS
import httpx
from bs4 import BeautifulSoup
import re

import asyncio
from app.database import archive_audible_response

def get_client():
    if not os.path.exists(AUDIBLE_AUTH_FILE):
        raise HTTPException(status_code=500, detail="Missing audible_auth.json")
    auth = audible.Authenticator.from_file(AUDIBLE_AUTH_FILE)
    return audible.Client(auth)

async def search_raw(query: str = None, author: str = None, isbn: str = None, limit: int = 5):
    """
    Supports General, Author, and ISBN search. Async wrapper.
    """
    try:
        # Build params dynamically based on what is provided
        params = {
            "num_results": limit,
            "products_sort_by": "Relevance",
            "response_groups": RESPONSE_GROUPS
        }

        if isbn: params["isbn"] = isbn
        elif author: params["search_author"] = author
        elif query: params["title"] = query
        else: return []

        def _sync_call():
            client = get_client()
            return client.get("catalog/products", params=params)

        results = await asyncio.to_thread(_sync_call)
        
        # Archive
        asyncio.create_task(archive_audible_response(
            endpoint="catalog/products",
            params=params, 
            response_data=results
        ))
        
        if results and results.get('products'):
            return results['products']
            
    except Exception as e:
        print(f"❌ Audible Search Error: {e}")
    return []

async def get_product_raw(asin: str):
    """
    Async wrapper for fetching product details.
    """
    def _sync_call():
        client = get_client()
        return client.get(f"catalog/products/{asin}", params={"response_groups": RESPONSE_GROUPS})
    
    try:
        resp = await asyncio.to_thread(_sync_call)
        
        # Archive
        asyncio.create_task(archive_audible_response(
            endpoint=f"catalog/products/{asin}",
            params={"asin": asin}, 
            response_data=resp
        ))
        
        return resp['product']
    except Exception as e:
        print(f"❌ Audible Details Error: {e}")
        raise e


async def scrape_list_from_url(url: str):
    """
    Scrapes an Audible HTML page for ASINs.
    Works for: Charts, Series Pages, Search Results, etc.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    asins = []
    title = "Imported List"
    
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers, timeout=15.0)
            if resp.status_code != 200:
                print(f"❌ Scrape Failed: {resp.status_code}")
                return None, []
            
            soup = BeautifulSoup(resp.content, "lxml")
            
            # 1. Extract Title
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
            else:
                title = soup.title.get_text(strip=True).replace("| Audible.com", "").strip()

            # 2. Extract ASINs
            # Audible lists usually have li items with 'data-asin' attribute
            # Strategy A: data-asin attribute (most reliable on desktop views)
            elements_with_asin = soup.select("[data-asin]")
            for el in elements_with_asin:
                asin = el.get("data-asin")
                if asin and len(asin) == 10 and asin not in asins:
                    asins.append(asin)
            
            # Strategy B: Fallback to regex in links if data-asin missing
            if not asins:
                links = soup.find_all("a", href=True)
                for link in links:
                    href = link['href']
                    # Match /pd/Title-Audiobook/B0xxxx or /pd/B0xxxx
                    match = re.search(r'/pd/.*(B0[A-Z0-9]{8})', href)
                    if match:
                        asin = match.group(1)
                        if asin not in asins:
                            asins.append(asin)

    except Exception as e:
        print(f"❌ List Scrape Exception: {e}")
        return None, []

    return title, asins