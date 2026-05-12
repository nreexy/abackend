import logging
import datetime
from .core import audible_archive_collection

logger = logging.getLogger(__name__)

async def archive_audible_response(endpoint: str, params: dict, response_data: dict, source: str = "api"):
    """
    Archives a raw Audible API response to MongoDB.
    fire-and-forget.
    """
    try:
        # Avoid archiving empty results if desired, or keep them for "no result" tracking
        # For now, archive everything.
        
        doc = {
            "stored_at": datetime.datetime.utcnow(),
            "endpoint": endpoint,
            "params": params,
            "source": source, # "api" or "scrape" if relevant
            "full_response": response_data
        }
        
        await audible_archive_collection.insert_one(doc)
        logger.debug(f"Archived Audible response for {endpoint}")
        
    except Exception as e:
        logger.error(f"Failed to archive Audible response: {e}")
