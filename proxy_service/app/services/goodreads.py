import json
import random
import re
import httpx
from bs4 import BeautifulSoup
from app.utils import normalize_language
import asyncio

# --- SEARCH SCRAPER (Results Page) ---
async def search_scraper(query: str):
    """
    Scrapes Goodreads Search Results.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    search_url = "https://www.goodreads.com/search"
    params = {"q": query, "search_type": "books"}
    results = []

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(search_url, params=params, headers=headers, timeout=8.0)
            if resp.status_code != 200: return []
            
            soup = BeautifulSoup(resp.content, "lxml")
            rows = soup.select("tr[itemscope]")
            
            for row in rows:
                try:
                    item = parse_search_row(row)
                    if item: results.append(item)
                except: continue
    except Exception as e:
        print(f"⚠️ Goodreads Scrape Failed: {e}")
        
    return results

def parse_search_row(row):
    # 1. Title & Link
    title_tag = row.select_one("a.bookTitle")
    if not title_tag: return None
    title = title_tag.get_text(strip=True)
    href = title_tag['href']
    
    match = re.search(r'/show/(\d+)', href)
    gr_id = "GR-" + match.group(1) if match else "GR-" + str(random.randint(100000, 999999))

    # 2. Author
    author_tag = row.select_one("a.authorName")
    author = author_tag.get_text(strip=True) if author_tag else "Unknown"

    # 3. Cover (FIXED REGEX)
    cover_tag = row.select_one("img.bookCover")
    cover = None
    if cover_tag:
        src = cover_tag.get('src', '')
        cover = clean_goodreads_cover_url(src)

    # 4. Rating
    minirating = row.select_one("span.minirating")
    rating_val, rating_count, pub_date = None, 0, None
    
    if minirating:
        text = minirating.get_text()
        if avg := re.search(r'(\d+\.\d+)\s+avg', text): rating_val = float(avg.group(1))
        if cnt := re.search(r'([\d,]+)\s+ratings', text): rating_count = int(cnt.group(1).replace(',', ''))
        if year := re.search(r'published\s+(\d{4})', text): pub_date = f"{year.group(1)}-01-01"

    return {
        "asin": gr_id,
        "title": title,
        "subtitle": None,
        "authors": [author],
        "narrators": [],
        "series": [],
        "publisher": None,
        "published_date": pub_date,
        "language": "en",
        "genres": [],
        "description": "See Goodreads for details.",
        "rating": rating_val,
        "rating_count": rating_count,
        "runtime_minutes": 0,
        "cover_image": cover,
        "sample_url": None,
        "chapters": [],
        "custom_metadata": {},
        "provider": "Goodreads"
    }

# --- LIST SCRAPER ---
async def scrape_list_from_url(url: str, max_pages: int = 200):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    all_books = []
    list_title = "Goodreads List"
    current_url = url
    page_count = 0

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        while current_url and page_count < max_pages:
            try:
                print(f"📖 Scraping Page {page_count + 1}: {current_url}")
                resp = await client.get(current_url, headers=headers)
                
                if resp.status_code != 200: 
                    print(f"❌ Status {resp.status_code} on page {page_count+1}")
                    break
                
                soup = BeautifulSoup(resp.content, "lxml")
                
                # ... [Title Logic matches previous version] ...
                if page_count == 0:
                    found_title = False
                    h1 = soup.select_one("h1.gr-h1--serif")
                    if not h1: h1 = soup.find("h1")
                    if h1:
                        text = h1.get_text(strip=True)
                        if text and text.lower() != "score":
                            list_title = text
                            found_title = True
                    if not found_title:
                        match = re.search(r'/list/show/\d+\.(.+?)([?#]|$)', url)
                        if match: list_title = match.group(1).replace('_', ' ')

                rows = soup.select("tr[itemscope]")
                if not rows: break

                for row in rows:
                    if book := parse_list_row(row):
                        if not any(b['asin'] == book['asin'] for b in all_books):
                            all_books.append(book)

                next_link = soup.select_one("a.next_page")
                if next_link and next_link.has_attr('href'):
                    current_url = "https://www.goodreads.com" + next_link['href']
                    page_count += 1
                    
                    # --- ADD DELAY HERE ---
                    # Random sleep between 1 and 3 seconds to be polite
                    delay = random.uniform(1.0, 3.0)
                    print(f"   zzz Sleeping {delay:.2f}s...")
                    await asyncio.sleep(delay)
                    # ----------------------
                    
                else: 
                    break
            except Exception as e:
                print(f"❌ Error scraping page {page_count}: {e}")
                break
                
    return list_title, all_books


def parse_list_row(row):
    try:
        title_tag = row.select_one("a.bookTitle")
        if not title_tag: return None
        title = title_tag.get_text(strip=True)
        href = title_tag['href']
        
        if match := re.search(r'/show/(\d+)', href): gr_id = "GR-" + match.group(1)
        else: return None

        author_tag = row.select_one("a.authorName")
        author = author_tag.get_text(strip=True) if author_tag else "Unknown"

        cover_tag = row.select_one("img.bookCover")
        cover = None
        if cover_tag:
            src = cover_tag.get('src', '')
            cover = clean_goodreads_cover_url(src)

        minirating = row.select_one("span.minirating")
        rating_val, rating_count = None, 0
        if minirating:
            text = minirating.get_text()
            if avg := re.search(r'(\d+\.\d+)\s+avg', text): rating_val = float(avg.group(1))
            if cnt := re.search(r'([\d,]+)\s+ratings', text): rating_count = int(cnt.group(1).replace(',', ''))

        return {
            "asin": gr_id,
            "title": title,
            "subtitle": None,
            "authors": [author],
            "narrators": [],
            "series": [],
            "publisher": None,
            "published_date": None,
            "language": "en",
            "genres": [],
            "description": "Imported from Goodreads List.",
            "rating": rating_val,
            "rating_count": rating_count,
            "runtime_minutes": 0,
            "cover_image": cover,
            "sample_url": None,
            "chapters": [],
            "custom_metadata": {},
            "provider": "Goodreads"
        }
    except: return None

# --- HELPER: COVER URL CLEANER ---
def clean_goodreads_cover_url(src: str) -> str:
    """
    Converts Goodreads thumbnails to full size images.
    """
    if not src: return None
    
    # 1. Remove the resize pattern
    # Removes ._SY75_, ._SX98_, ._SX50_, etc.
    cover = re.sub(r'\._S[XY]\d+_', '', src)
    
    # 2. Remove old style 's' suffix (e.g. 12345s.jpg -> 12345.jpg)
    # Only removes 's' if it is preceded by digits and followed by .jpg
    cover = re.sub(r'(?<=\d)s(?=\.jpg)', '', cover)
    
    # 3. Safety: Fix double dots
    cover = cover.replace('..', '.')
    
    return cover

def parse_goodreads_page(html_content, url):
    """Parses the HTML of a Goodreads Book Page (New & Old Design compatible)"""
    soup = BeautifulSoup(html_content, "lxml")
    data = {}
    
    # 1. Try JSON-LD (Structured Data)
    script = soup.find("script", {"type": "application/ld+json"})
    if script:
        try: data = json.loads(script.string)
        except: pass
    
    # --- EXTRACT GENRES (The Fix) ---
    genres = []
    
    # Strategy A: New Design (data-testid)
    # The container is usually <div data-testid="genresList">...<ul>...<li>...<a ...>Genre</a>
    genre_list_div = soup.find("div", {"data-testid": "genresList"})
    if genre_list_div:
        # Find all links inside buttons/spans in this list
        # Usually just <a href="/genres/...">GenreName</a>
        links = genre_list_div.find_all("a")
        for link in links:
            g_name = link.get_text(strip=True)
            # Filter out "show more" links
            if g_name and "..." not in g_name:
                genres.append(g_name)
    
    # Strategy B: Old Design (sidebar)
    if not genres:
        genre_links = soup.select(".bookPageGenreLink")
        genres = [g.get_text(strip=True) for g in genre_links]

    # Limit to top 5 genres to keep UI clean
    genres = list(dict.fromkeys(genres))[:5] 

    # --- EXTRACT OTHER FIELDS ---

    # ID
    gr_id = "GR-" + url.split("/")[-1].split("-")[0]
    if gr_id == "GR-": gr_id = "GR-" + str(random.randint(100000, 999999))

    # Title
    title = data.get("name")
    if not title:
        t_tag = soup.find("h1", {"data-testid": "bookTitle"})
        if t_tag: title = t_tag.get_text(strip=True)

    # Authors
    authors = []
    ad = data.get("author")
    if isinstance(ad, list): authors = [a.get("name") for a in ad if a.get("name")]
    elif isinstance(ad, dict): authors = [ad.get("name")]
    if not authors:
        # Fallback selector
        authors = [a.get_text(strip=True) for a in soup.select(".ContributorLink__name")]

    # Description
    description = ""
    desc_div = soup.find("div", {"data-testid": "description"})
    if desc_div:
        description = desc_div.get_text("\n")
    else:
        desc_div = soup.select_one("#descriptionContainer span[style]")
        if desc_div: description = desc_div.get_text("\n")

    # Rating
    rating_val, rating_count = None, 0
    agg = data.get("aggregateRating", {})
    if agg:
        rating_val = float(agg.get("ratingValue", 0))
        rating_count = int(agg.get("ratingCount", 0))

    # Series (Parsing from Title or Specific Element)
    series_list = []
    # New Design Series Link often looks like: <h3><a>Series Name #1</a></h3>
    series_link = soup.select_one("a[href*='/series/']")
    if series_link:
        full_text = series_link.get_text(strip=True)
        # Try to split "Name #1"
        match = re.search(r'(.*?)\s+#([\d\.]+)', full_text)
        if match:
            series_list.append({"name": match.group(1), "sequence": match.group(2)})
        else:
            series_list.append({"name": full_text, "sequence": ""})

    # Result Object
    return [{
        "asin": gr_id,
        "title": title or "Unknown",
        "subtitle": None,
        "authors": authors,
        "narrators": [],
        "series": series_list,
        "publisher": data.get("publisher", {}).get("name"),
        "published_date": None,
        "language": normalize_language(data.get("inLanguage")),
        "genres": genres, # <--- Updated
        "description": description,
        "rating": rating_val,
        "rating_count": rating_count,
        "runtime_minutes": 0, 
        "cover_image": data.get("image"),
        "sample_url": None,
        "chapters": [],
        "custom_metadata": {},
        "provider": "Goodreads"
    }]