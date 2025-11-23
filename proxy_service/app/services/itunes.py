# app/services/itunes.py
import html
import httpx
from app.utils import normalize_language


async def search_raw(query: str = None, author: str = None, isbn: str = None, limit: int = 5):
    url = "https://itunes.apple.com/search"
    
    # Default params
    params = {
        "media": "audiobook",
        "entity": "audiobook", 
        "limit": limit
    }

    if isbn:
        # iTunes doesn't handle ISBN search well via API, usually requires UPC.
        # We try searching the ISBN as a general term.
        params["term"] = isbn
    elif author:
        params["term"] = author
        params["attribute"] = "authorTerm" # Restrict to author
    elif query:
        params["term"] = query

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                return [format_result(item) for item in data.get("results", [])]
    except Exception: pass
    return []



async def fetch_details(itunes_id: str):
    url = "https://itunes.apple.com/lookup"
    params = {"id": itunes_id}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                if data["resultCount"] > 0:
                    return format_result(data["results"][0])
    except Exception: pass
    return None

def format_result(item):
    cover = item.get("artworkUrl100", "").replace("100x100bb", "600x600bb")
    return {
        "asin": str(item.get("collectionId")),
        "title": item.get("collectionName"),
        "subtitle": None,
        "authors": [item.get("artistName")],
        "narrators": [],
        "series": [],
        "publisher": item.get("copyright"),
        "published_date": item.get("releaseDate", "")[:10],
"language": normalize_language(item.get("language", "en")),
        "genres": [item.get("primaryGenreName")],
        "description": html.unescape(item.get("description", "")),
        "rating": None,
        "rating_count": 0,
        "runtime_minutes": int(item.get("trackTimeMillis", 0) / 1000 / 60),
        "cover_image": cover,
        "sample_url": item.get("previewUrl"),
        "chapters": [],
        "custom_metadata": {},
        "provider": "iTunes"
    }