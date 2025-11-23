import os

# URLs
AUDNEXUS_URL = os.getenv("AUDNEXUS_URL", "http://audnexus:3000")

# Audible Settings
AUDIBLE_AUTH_FILE = "audible_auth.json"
RESPONSE_GROUPS = (
    "product_attrs,product_desc,product_extended_attrs,"
    "media,contributors,rating,series,category_ladders,"
    "sample"
)