from app.database import find_unified_by_relation, create_unified_book, add_relation_to_unified_book
from fuzzywuzzy import fuzz

async def unify_search_results(results_list: list):
    """
    Takes a list of raw result lists (one from each provider).
    Returns a unified list of books.
    """
    # 1. Flatten all results
    all_books = []
    for provider_results in results_list:
        if provider_results:
            all_books.extend(provider_results)

    if not all_books: return []

    unified_map = {} # Map temporary ID to Unified Object
    unassigned = []

    # 2. Resolve against DB
    # For performance, we could batch query, but for now we iterate (N is small ~30)
    for book in all_books:
        provider = book.get("provider")
        pid = book.get("asin") # This is the provider ID (e.g. B08... or hc:...)
        
        # Check if already unified
        existing = await find_unified_by_relation(provider, pid)
        
        if existing:
            # Add to map
            uid = existing["_id"]
            if uid not in unified_map:
                unified_map[uid] = {
                    "master": existing,
                    "sources": []
                }
            unified_map[uid]["sources"].append(book)
        else:
            unassigned.append(book)

    # 3. Auto-Link (Heuristic Matching)
    # Compare unassigned against each other AND against existing Unified Groups
    
    # Simple Grouping Logic for Unassigned
    # We group them by a "Match Key" -> (Clean Title, Author)
    potential_groups = {}
    
    for book in unassigned:
        title_slug = _make_slug(book.get("title", ""))
        # Get first author slug
        auths = book.get("authors", [])
        author_slug = _make_slug(auths[0]) if auths else "unknown"
        
        match_key = f"{title_slug}|{author_slug}"
        
        # Also check ISBN if available (Strong Match)
        isbn = book.get("isbn")
        if isbn and len(str(isbn)) > 9:
             match_key = f"isbn:{isbn}"

        if match_key not in potential_groups:
            potential_groups[match_key] = []
        potential_groups[match_key].append(book)

    # 4. Create New Unified Objects for Groups
    final_output = []
    
    # Process DB matches first
    for uid, data in unified_map.items():
        merged = _merge_sources(data["sources"], data["master"])
        final_output.append(merged)

    # Process New Groups
    for key, group in potential_groups.items():
        if not group: continue
        
        # If group has multiple items, or even 1 item that is new, we treat it as a new Unified Entity
        # (Or we just pass it through if it's single, but the goal is to Unify everything)
        
        # Create Master Record
        primary = group[0] # Pick first as representative
        relations = [{"provider": b.get("provider"), "id": b.get("asin")} for b in group]
        
        # Persist new Unified Book
        new_master = await create_unified_book(
            title=primary.get("title"),
            authors=primary.get("authors", []),
            relations=relations
        )
        
        merged = _merge_sources(group, new_master)
        final_output.append(merged)

    return final_output

def _make_slug(text):
    if not text: return ""
    return "".join(e for e in text.lower() if e.isalnum())

def _merge_sources(sources, master_record):
    """
    Merges a list of source books into a single presentation object.
    Prioritizes Audible > Hardcover > Google > Others.
    """
    priority = {"Audible": 10, "Hardcover": 8, "Penguin Random House": 6, "iTunes": 5, "Google Books": 4, "Goodreads": 2}
    
    # Sort sources by priority
    sources.sort(key=lambda x: priority.get(x.get("provider"), 1), reverse=True)
    
    primary = sources[0]
    
    # Start with Primary Data
    merged = primary.copy()
    
    # Override ID with Unified ID (so UI links to /book/unified:UUID)
    # actually, wait - for now let's keep the Primary ID as the main link 
    # but add a "rel" field.
    # The UI expects an ASIN to fetch details. If we change ASIN to UUID, 
    # fetch_details needs to handle it. 
    # For this MVP, let's keep the Primary ASIN as the identifier, but decorate with badges.
    
    merged["unified_id"] = master_record["_id"]
    
    # Collect all providers
    all_providers = set()
    for s in sources:
        if s.get("provider"):
            all_providers.add(s.get("provider"))
            
    merged["available_providers"] = list(all_providers)
    
    return merged
