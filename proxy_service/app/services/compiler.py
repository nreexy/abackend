import html
import httpx
from app.config import AUDNEXUS_URL
from app.database import get_custom_fields
from app.utils import deep_find_rating, deep_find_count, normalize_language


async def compile_audible_metadata(asin: str, p: dict):
    """Converts raw Audible JSON to Standard JSON"""
    
    # Series
    series_list = [{"name":s["title"],"sequence":s.get("sequence")} for s in p.get("series",[])]
    
    # Genres
    genres = [l["ladder"][-1]["name"] for l in p.get("category_ladders",[]) if l.get("ladder")]
    
    # Runtime
    runtime = p.get("runtime_length_min")
    if runtime is None:
        ad = p.get("asset_details", {})
        if "run_time" in ad: runtime = ad["run_time"]
        elif "length_ms" in ad: runtime = int(ad["length_ms"] / 60000)
    
    # Chapters (Fetch from Audnexus)
    chapters = []
    try:
        async with httpx.AsyncClient() as h:
            r = await h.get(f"{AUDNEXUS_URL}/books/{asin}/chapters", timeout=2.0)
            if r.status_code == 200: chapters = r.json()
    except: pass

    return {
        "asin": asin,
        "title": p.get("title"),
        "subtitle": p.get("subtitle"),
        "authors": [a['name'] for a in p.get("authors", [])],
        "narrators": [n['name'] for n in p.get("narrators", [])],
        "series": series_list,
        "publisher": p.get("publisher_name"),
        "published_date": p.get("release_date"),
 "language": normalize_language(p.get("language")),
        "genres": genres,
        "description": html.unescape(p.get("publisher_summary") or ""),
        "rating": deep_find_rating(p),
        "rating_count": deep_find_count(p),
        "runtime_minutes": runtime,
        "cover_image": p.get("product_images", {}).get("500"),
        "sample_url": p.get("sample_url"),
        "chapters": chapters,
        "custom_metadata": await get_custom_fields(asin) or {},
        "provider": "Audible"
    }