import httpx
import json

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"

def _parse_hardcover_book(item):
    """
    Parses a single Hardcover Book item into our internal format.
    """
    # Authors
    authors = []
    for contribution in item.get("contributions", []):
        if contribution.get("author") and contribution.get("author", {}).get("name"):
            authors.append(contribution["author"]["name"])

    # Image
    cover_image = None
    images = item.get("images", [])
    if images:
        # Prefer 'poster' or just first
        for img in images:
            if img.get("url"):
                cover_image = img["url"]
                break

    return {
        "asin": f"hc:{item.get('slug') or item.get('id')}", # Custom ID prefix
        "title": item.get("title"),
        "subtitle": item.get("subtitle"),
        "authors": authors,
        "narrators": [], 
        "description": item.get("description"),
        "genres": [g.get("genre", {}).get("name") for g in item.get("book_genres", []) if g.get("genre", {}).get("name")],
        "release_date": item.get("release_date"),
        "publisher": None, # Data often missing or deep in edition
        "language": None,
        "cover_image": cover_image,
        "rating": item.get("rating"),
        "rating_count": item.get("users_count"),
        "provider": "Hardcover",
        "series": [] 
    }

async def search_book(query: str, api_key: str, limit: int = 5):
    """
    Searches Hardcover.app via GraphQL.
    """
    if not api_key:
        return []

    # Create a simple slug from the query for fallback search
    # e.g. "Project Hail Mary" -> "project-hail-mary"
    import re
    slug_query = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')

    # GraphQL Query
    # Using _eq on title (exact) AND slug (approximate case-insensitive)
    gql_query = """
    query SearchBooks($title: String!, $slug: String!, $limit: Int!) {
      books(
        where: {_or: [{title: {_eq: $title}}, {slug: {_eq: $slug}}]}
        limit: $limit
        order_by: {users_count: desc}
      ) {
        id
        slug
        title
        subtitle
        description
        release_date
        rating
        users_count
        contributions {
          author {
            name
          }
        }
        images {
          url
        }
      }
    }
    """
    
    # Ensure Bearer prefix is handled correctly
    clean_key = api_key.replace("Bearer", "").strip()
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json"
    }

    variables = {
        "title": query.strip(),
        "slug": slug_query,
        "limit": limit
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                HARDCOVER_API_URL, 
                json={"query": gql_query, "variables": variables}, 
                headers=headers, 
                timeout=10.0
            )
            
            if resp.status_code != 200:
                print(f"❌ Hardcover API Error: {resp.status_code} - {resp.text}")
                return []
            
            data = resp.json()
            if "errors" in data:
                print(f"❌ Hardcover GraphQL Error: {data['errors']}")
                return []

            books = data.get("data", {}).get("books", [])
            
            results = []
            for item in books:
                parsed = _parse_hardcover_book(item)
                if parsed:
                    results.append(parsed)
            return results
            
        except Exception as e:
            print(f"❌ Hardcover Search Exception: {e}")
            return []
