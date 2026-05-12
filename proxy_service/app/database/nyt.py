import datetime
from .core import nyt_subscriptions_collection, nyt_archive_collection, nyt_book_archive_collection
import logging

logger = logging.getLogger(__name__)

async def upsert_nyt_subscription(list_name_encoded: str, display_name: str, enabled: bool = True):
    """
    Saves or updates a NYT list subscription.
    """
    await nyt_subscriptions_collection.update_one(
        {"list_name_encoded": list_name_encoded},
        {
            "$set": {
                "display_name": display_name,
                "enabled": enabled,
                "updated_at": datetime.datetime.utcnow()
            },
            "$setOnInsert": {
                "created_at": datetime.datetime.utcnow(),
                "last_run": None
            }
        },
        upsert=True
    )

async def get_all_nyt_subscriptions(enabled_only: bool = True):
    query = {"enabled": True} if enabled_only else {}
    return await nyt_subscriptions_collection.find(query).to_list(length=None)

async def update_nyt_subscription_last_run(list_name_encoded: str):
    await nyt_subscriptions_collection.update_one(
        {"list_name_encoded": list_name_encoded},
        {"$set": {"last_run": datetime.datetime.utcnow()}}
    )

async def delete_nyt_subscription(list_name_encoded: str):
    await nyt_subscriptions_collection.delete_one({"list_name_encoded": list_name_encoded})

async def archive_nyt_response(endpoint: str, params: dict, response_data: dict):
    """
    Archives individual books from NYT API response to MongoDB.
    """
    try:
        # 1. Archive Raw Response (optional, but good for backup/debugging)
        # Using the old collection for raw data just in case
        try:
            raw_doc = {
                "stored_at": datetime.datetime.utcnow(),
                "endpoint": endpoint,
                "params": params,
                "full_response": response_data
            }
            await nyt_archive_collection.insert_one(raw_doc)
        except Exception as e:
            logger.error(f"Failed to archive RAW NYT response: {e}")

        # 2. Extract and Archive Individual Books
        results = response_data.get("results")
        if not results: return

        # Handle different response structures
        # A. lists/overview.json -> results { lists: [...] }
        # B. lists/{date}/{list}.json -> results { books: [...] } (if we use that)
        # Assuming we mostly use lists/overview or lists/current based on previous code.
        
        # Normalize to list of lists
        lists_to_process = []
        common_metadata = {
            "bestsellers_date": results.get("bestsellers_date"),
            "published_date": results.get("published_date")
        }

        if "lists" in results:
            lists_to_process = results["lists"]
        elif "books" in results:
            # Single list response (e.g. get_list_details often returns this structure)
            # We might need to construct a "list" object wrapper
            lists_to_process = [results] 
        
        book_docs = []
        
        for lst in lists_to_process:
            list_name = lst.get("list_name")
            list_name_encoded = lst.get("list_name_encoded")
            list_id = lst.get("list_id")
            display_name = lst.get("display_name")
            
            books = lst.get("books", [])
            for book in books:
                doc = {
                    "stored_at": datetime.datetime.utcnow(),
                    "source_endpoint": endpoint,
                    
                    # List Info
                    "nyt_list_name": list_name,
                    "nyt_list_name_encoded": list_name_encoded,
                    "nyt_list_id": list_id,
                    "nyt_display_name": display_name,
                    
                    # Dates
                    "bestsellers_date": common_metadata["bestsellers_date"],
                    "published_date": common_metadata["published_date"],
                    
                    # Book Details
                    "rank": book.get("rank"),
                    "rank_last_week": book.get("rank_last_week"),
                    "weeks_on_list": book.get("weeks_on_list"),
                    "asterisk": book.get("asterisk"),
                    "dagger": book.get("dagger"),
                    
                    "primary_isbn13": book.get("primary_isbn13"),
                    "primary_isbn10": book.get("primary_isbn10"),
                    
                    "title": book.get("title"),
                    "author": book.get("author"),
                    "description": book.get("description"),
                    "publisher": book.get("publisher"),
                    "price": book.get("price"),
                    "age_group": book.get("age_group"),
                    "contributor": book.get("contributor"),
                    
                    "book_image": book.get("book_image"),
                    "book_uri": book.get("book_uri"),
                    "amazon_product_url": book.get("amazon_product_url"),
                    
                    "buy_links": book.get("buy_links", []),
                    "isbns": book.get("isbns", [])
                }
                book_docs.append(doc)
        
        if book_docs:
            await nyt_book_archive_collection.insert_many(book_docs)
            logger.debug(f"Archived {len(book_docs)} NYT books from {endpoint}")
            
    except Exception as e:
        logger.error(f"Failed to archive NYT books: {e}")
