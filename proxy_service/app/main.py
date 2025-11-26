import sys
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# --- RATE LIMITING IMPORTS (Fixes your error) ---
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.routers import api, ui
from app.database import init_db_indexes
from app.limiter import limiter

# --- LOGGING CONFIGURATION ---
LOG_FILE = "system.log"

# 1. Configure File Logging
file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
formatter = logging.Formatter("%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler.setFormatter(formatter)

# 2. Attach to Uvicorn (Server Logs)
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.addHandler(file_handler)

error_logger = logging.getLogger("uvicorn.error")
error_logger.addHandler(file_handler)

# 3. Capture 'print()' statements
class PrintLogger:
    def __init__(self, logger, level=logging.INFO):
        self.logger = logger
        self.level = level
        self.terminal = sys.__stdout__

    def write(self, message):
        self.terminal.write(message)
        if message.strip():
            self.logger.log(self.level, message.strip())

    def flush(self):
        self.terminal.flush()

sys.stdout = PrintLogger(error_logger, logging.INFO)

# --- APP DEFINITION ---
app = FastAPI(title="Audiobook Metadata Proxy")

# --- SECURITY & MIDDLEWARE ---

# 1. Rate Limiter (Connects to Redis)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 2. Trusted Hosts
app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["*"] # Restrict this in production if needed
)

# 3. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- EVENTS & ROUTES ---

@app.on_event("startup")
async def startup_db_client():
    await init_db_indexes()
    print(f"📝 System Logging fully initialized to {LOG_FILE}")

app.include_router(api.router)
app.include_router(ui.router)

@app.get("/")
async def health_check():
    return {"status": "online", "storage": "MongoDB"}