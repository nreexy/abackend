import httpx
import urllib.parse

GOOGLE_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"

def _parse_google_book(item):
    """
    Parses a single Google Book item into our internal format.
    """
    vol_info = item.get("volumeInfo", {})
    
    # Authors
    authors = vol_info.get("authors", [])
    
    # Image
    image_links = vol_info.get("imageLinks", {})
    cover_image = image_links.get("extraLarge") or image_links.get("large") or image_links.get("medium") or image_links.get("thumbnail")
    if cover_image:
        cover_image = cover_image.replace("http://", "https://")

    # Identifiers (ISBN)
    isbn = None
    for ident in vol_info.get("industryIdentifiers", []):
        if ident.get("type") == "ISBN_13":
            isbn = ident.get("identifier")
            break
    
    # Fallback to ID if no ISBN
    asin = isbn if isbn else item.get("id")

    return {
        "asin": asin,
        "title": vol_info.get("title"),
        "subtitle": vol_info.get("subtitle"),
        "authors": authors,
        "narrators": [], # Google Books rarely has narrator info
        "description": vol_info.get("description"),
        "genres": vol_info.get("categories", []),
        "release_date": vol_info.get("publishedDate"),
        "publisher": vol_info.get("publisher"),
        "language": vol_info.get("language"),
        "cover_image": cover_image,
        "rating": vol_info.get("averageRating"),
        "rating_count": vol_info.get("ratingsCount"),
        "provider": "Google Books",
        "series": [] # Series info is complex in Google Books, skipping for now
    }

async def search_book(query: str, api_key: str, limit: int = 5):
    """
    Searches Google Books API.
    """
    if not api_key:
        return []

    params = {
        "q": query,
        "maxResults": limit,
        "key": api_key,
        "printType": "books" # Focus on books
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(GOOGLE_BOOKS_API_URL, params=params, timeout=10.0)
            if resp.status_code != 200:
                print(f"❌ Google Books API Error: {resp.status_code} - {resp.text}")
                return []
            
            data = resp.json()
            items = data.get("items", [])
            
            results = []
            for item in items:
                parsed = _parse_google_book(item)
                if parsed:
                    results.append(parsed)
            return results
            
        except Exception as e:
            print(f"❌ Google Books Search Exception: {e}")
            return []

async def get_book_details(volume_id: str, api_key: str):
    """
    Fetches details for a specific volume ID.
    """
    if not api_key:
        return None
        
    url = f"{GOOGLE_BOOKS_API_URL}/{volume_id}"
    params = {"key": api_key}
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params=params, timeout=10.0)
            if resp.status_code != 200:
                return None
            
            data = resp.json()
            return _parse_google_book(data)
            
        except Exception as e:
            print(f"❌ Google Books Details Exception: {e}")
            return None
