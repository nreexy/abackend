import httpx
import html
import json
from app.config import PRH_API_KEY
from app.utils import normalize_language

# Base URL for the Enhanced API
BASE_URL = "https://api.penguinrandomhouse.com/resources/v2/title/domains/PRH.US"


async def search_raw(query: str, limit: int = 5):
    """
    Search PRH API using the 'search-display' view.
    """
    # FIX: Use the specific search view endpoint, not the raw titles list
    url = f"{BASE_URL}/search/views/search-display"
    
    params = {
        "api_key": PRH_API_KEY,
        "q": query,
        "rows": limit,
        "docType": "audiobook",
        "sort": "relevancy"

    }
    
    # Use a browser-like User-Agent to avoid blocks
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json"
    }

    print(f"🚀 PRH SEARCH: {query}")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, headers=headers, timeout=10.0)
            
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    # The search view structure is slightly different (often flattened)
                    # We look for 'data.titles' or 'data.results'
                    if "data" in data and "titles" in data["data"]:
                        titles = data["data"]["titles"]
                        print(f"   ✅ PRH Items Found: {len(titles)}")
                        return [format_prh_result(item) for item in titles]
                    elif "data" in data and "results" in data["data"]:
                         # Sometimes key is 'results' in search views
                        titles = data["data"]["results"]
                        print(f"   ✅ PRH Items Found: {len(titles)}")
                        return [format_prh_result(item) for item in titles]
                    else:
                        # Debug structure if keys differ
                        print(f"   ⚠️ PRH Empty/Unknown Structure. Keys: {list(data.get('data', {}).keys())}")
                        return []
                except json.JSONDecodeError:
                    print("   ❌ PRH Decode Error")
            elif resp.status_code == 404:
                # Fallback: Try Legacy URL if Enhanced fails
                print("   ⚠️ Enhanced URL 404, trying Legacy Search...")
                return await search_legacy_fallback(query, headers)
            else:
                print(f"   ❌ PRH Failed: {resp.status_code}")

    except Exception as e:
        print(f"   ⚠️ PRH Exception: {e}")
    
    return []

async def search_legacy_fallback(query: str, headers: dict):
    """Fallback to the RESTSTOP host if the API gateway fails"""
    url = "https://reststop.randomhouse.com/resources/titles"
    params = {"search": query, "docType": "audiobook", "rows": 5}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                if "title" in data: # Legacy API returns dict with 'title' list directly or in 'titles'
                    titles = data["title"] if isinstance(data["title"], list) else [data["title"]]
                    return [format_prh_result(item) for item in titles]
    except Exception as e:
        print(f"   ❌ Legacy Search Failed: {e}")
    return []

async def fetch_details(isbn: str):
    """
    Lookup specific ISBN. 
    We use the 'product-display' view for richer details.
    """
    # FIX: Use the product-display view which is more likely to have metadata
    url = f"{BASE_URL}/titles/{isbn}/views/product-display"
    params = {"api_key": PRH_API_KEY}

    print(f"🚀 PRH DETAILS: {isbn}")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10.0)
            
            if resp.status_code == 200:
                data = resp.json()
                # Product display view usually returns a single object in 'data' or 'data.titles'
                if "data" in data and "titles" in data["data"]:
                    items = data["data"]["titles"]
                    if items:
                        return format_prh_result(items[0])
            elif resp.status_code == 404:
                # Try raw title resource if view fails
                return await fetch_details_raw(isbn)

    except Exception as e:
        print(f"   ⚠️ PRH Detail Exception: {e}")
    return None

async def fetch_details_raw(isbn: str):
    """Fallback to raw title resource"""
    url = f"{BASE_URL}/titles/{isbn}"
    params = {"api_key": PRH_API_KEY}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                if "data" in data and "titles" in data["data"]:
                    return format_prh_result(data["data"]["titles"][0])
    except: pass
    return None

def format_prh_result(item):
    """Map PRH JSON to Standard Schema"""
    isbn = item.get("isbn")
    title = item.get("titleweb")
    subtitle = item.get("subtitle")
    
    # Author can be "authorweb" (string) or list
    authors = []
    aw = item.get("authorweb")
    if aw:
        authors = [aw]

    desc_html = item.get("flapcopy") or ""
    description = html.unescape(desc_html)
    
    series_list = []
    if item.get("series"):
        s_name = item.get("series")
        s_seq = item.get("seriesnumber")
        if s_name:
            series_list.append({"name": s_name, "sequence": s_seq})

    # Consistent cover URL
    cover = f"https://images.randomhouse.com/cover/{isbn}"

    # Runtime extraction (sometimes in 'pages' for audio, or 'runTime')
    runtime = 0 
    if item.get("pages"): 
        # PRH Audio sometimes maps minutes to pages field in search views
        try: runtime = int(item.get("pages"))
        except: pass

    pub_date = item.get("onsaledate")
    if pub_date:
        pub_date = pub_date[:10]

    return {
        "asin": isbn, 
        "title": title,
        "subtitle": subtitle,
        "authors": authors,
        "narrators": [],
        "series": series_list,
        "publisher": item.get("imprint"),
        "published_date": pub_date,
        "language": normalize_language("en"),
        "genres": [], 
        "description": description,
        "rating": None, 
        "rating_count": 0,
        "runtime_minutes": runtime,
        "cover_image": cover,
        "sample_url": None,
        "chapters": [],
        "custom_metadata": {},
        "provider": "Penguin Random House"
    }