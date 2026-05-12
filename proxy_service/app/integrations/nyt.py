import httpx
import logging
from typing import List, Dict, Optional
import urllib.parse
from app.database import get_system_settings, archive_nyt_response
import asyncio

logger = logging.getLogger(__name__)

NYT_BASE_URL = "https://api.nytimes.com/svc/books/v3"

class NYTClient:
    def __init__(self):
        self.api_key = None

    async def _get_api_key(self):
        if not self.api_key:
            settings = await get_system_settings()
            self.api_key = settings.get("nyt_api_key")
        return self.api_key

    async def get_list_names(self) -> List[Dict]:
        """Fetches all available NYT Book Lists using overview.json."""
        api_key = await self._get_api_key()
        if not api_key:
            logger.warning("NYT API Key not configured.")
            return []

        # User reported names.json fails, using overview.json
        endpoint = "/lists/overview.json"
        url = f"{NYT_BASE_URL}{endpoint}?api-key={api_key}"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                # Archive in background
                asyncio.create_task(archive_nyt_response(
                    endpoint="lists/overview",
                    params={"api_key_masked": "true"},
                    response_data=data
                ))
                
                # overview.json structure: { results: { lists: [] } }
                return data.get("results", {}).get("lists", [])
            except Exception as e:
                logger.error(f"Error fetching NYT lists: {e}")
                return []

    async def get_list_details(self, list_name_encoded: str) -> List[Dict]:
        """
        Fetches the current list details for a given list name.
        list_name_encoded: e.g. "hardcover-fiction"
        """
        api_key = await self._get_api_key()
        if not api_key:
            return []

        # The endpoint is /lists/current/{list_name}.json
        endpoint = f"/lists/current/{list_name_encoded}.json"
        url = f"{NYT_BASE_URL}{endpoint}?api-key={api_key}"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                # Archive in background
                asyncio.create_task(archive_nyt_response(
                    endpoint=f"lists/current/{list_name_encoded}",
                    params={"list_name": list_name_encoded},
                    response_data=data
                ))
                
                results = data.get("results", {})
                books = results.get("books", [])
                return books
            except Exception as e:
                logger.error(f"Error fetching NYT list details for {list_name_encoded}: {e}")
                return []
