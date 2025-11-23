import sys
import logging
from fastapi import FastAPI
from app.routers import api, ui
from app.database import init_db_indexes

LOG_FILE = "system.log"

# --- 1. CONFIGURE FILE LOGGING ---
# Create a file handler that writes to system.log
file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
# Format: Time - Message
formatter = logging.Formatter("%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler.setFormatter(formatter)

# --- 2. ATTACH TO UVICORN ---
# This captures the web server logs (GET /search 200 OK)
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.addHandler(file_handler)

# Also capture application errors
error_logger = logging.getLogger("uvicorn.error")
error_logger.addHandler(file_handler)

# --- 3. CAPTURE 'print()' STATEMENTS ---
# This captures your manual debug prints (🚀 PRH SEARCH...)
class PrintLogger:
    def __init__(self, logger, level=logging.INFO):
        self.logger = logger
        self.level = level
        self.terminal = sys.__stdout__ # Keep original stdout to print to Docker console

    def write(self, message):
        # Write to Docker Console
        self.terminal.write(message)
        
        # Write to File (via Logger)
        if message.strip(): # Avoid logging empty newlines
            self.logger.log(self.level, message.strip())

    def flush(self):
        self.terminal.flush()

# Redirect stdout to our logger
# We use the 'uvicorn.error' logger for prints so they appear in the same stream
sys.stdout = PrintLogger(error_logger, logging.INFO)

app = FastAPI(title="Audiobook Metadata Proxy")

@app.on_event("startup")
async def startup_db_client():
    await init_db_indexes()
    print(f"📝 System Logging fully initialized to {LOG_FILE}")

app.include_router(ui.router)
app.include_router(api.router)

@app.get("/")
async def health_check():
    return {"status": "online", "storage": "MongoDB"}