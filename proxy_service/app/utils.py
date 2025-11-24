import os
from collections import deque
import hashlib
from app.config import SECRET_KEY

# Map of common full names to ISO codes
LANGUAGE_MAP = {
    "english": "en",
    "german": "de",
    "deutsch": "de",
    "french": "fr",
    "francais": "fr",
    "spanish": "es",
    "espanol": "es",
    "italian": "it",
    "italiano": "it",
    "japanese": "ja",
    "chinese": "zh",
    "mandarin": "zh",
    "russian": "ru",
    "portuguese": "pt",
    "polish": "pl",
    "dutch": "nl",
    "swedish": "sv",
    "danish": "da",
    "finnish": "fi",
    "norwegian": "no",
    "korean": "ko"
}

def normalize_language(lang_str: str) -> str:
    """
    Normalizes language strings to 2-letter ISO code (e.g., 'English' -> 'en').
    """
    if not lang_str:
        return "en" # Default fallback

    cleaned = str(lang_str).lower().strip()

    # 1. Handle Region Codes (e.g., 'en-US', 'en_GB' -> 'en')
    if '-' in cleaned:
        cleaned = cleaned.split('-')[0]
    if '_' in cleaned:
        cleaned = cleaned.split('_')[0]

    # 2. If already 2 characters, assume it's a code
    if len(cleaned) == 2:
        return cleaned

    # 3. Map Full Names -> Codes
    return LANGUAGE_MAP.get(cleaned, "en") # Return 'en' if mapping not found



def deep_find_rating(data):
    """Recursively search for rating > 0 in Dicts AND Lists"""
    if isinstance(data, dict):
        candidates = ["average_rating", "overall_distribution_average", "score"]
        for key in candidates:
            val = data.get(key)
            if isinstance(val, (int, float)) and val > 0: return val
        for value in data.values():
            found = deep_find_rating(value)
            if found: return found
    elif isinstance(data, list):
        for item in data:
            found = deep_find_rating(item)
            if found: return found
    return None

def deep_find_count(data):
    """Recursively search for counts in Dicts AND Lists"""
    if isinstance(data, dict):
        if data.get("num_ratings"): return data["num_ratings"]
        if data.get("num_reviews"): return data["num_reviews"]
        for value in data.values():
            found = deep_find_count(value)
            if found: return found
    elif isinstance(data, list):
        for item in data:
            found = deep_find_count(item)
            if found: return found
    return 0


def get_system_logs(limit: int = 200):
    """
    Reads the last N lines from system.log.
    """
    filename = "system.log"
    if not os.path.exists(filename):
        return ["Log file not created yet."]
    
    try:
        with open(filename, "r", encoding="utf-8") as f:
            # deque(file, maxlen=N) is an efficient way to get tail lines
            last_lines = deque(f, maxlen=limit)
            return list(last_lines)
    except Exception as e:
        return [f"Error reading logs: {str(e)}"]


def get_device_hash(ip: str) -> str:
    """
    Anonymizes an IP address using a Salted Hash.
    Returns the first 12 characters of the hash (enough for uniqueness).
    """
    if not ip or ip == "Unknown" or ip == "127.0.0.1":
        return "Localhost"
    
    # Combine IP with Secret Key to prevent rainbow table attacks
    raw = f"{ip}-{SECRET_KEY}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]