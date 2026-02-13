import time
import datetime
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.database import log_request_access, is_client_blocked, add_block, get_system_settings, get_country_code, redis_client
from starlette.responses import JSONResponse

# Sensitive Paths that trigger IMMEDIATE BAN
SENSITIVE_PATHS = [
    "/.env", "/.git", "/wp-config.php", "/.aws", "/id_rsa", "/shadow", 
    "/.ssh", "/.bash_history", "/.config", "/.local"
]

# Honeypot Paths — no legitimate user should ever access these
HONEYPOT_PATHS = [
    "/admin", "/wp-admin", "/wp-login.php", "/phpmyadmin", "/pma",
    "/administrator", "/xmlrpc.php", "/cgi-bin", "/.well-known/security.txt",
    "/solr", "/actuator", "/console", "/manager", "/jmx-console"
]

# Only these HTTP methods are allowed for this application
ALLOWED_METHODS = {"GET", "POST", "HEAD", "OPTIONS"}

# Rate limit: max requests per window (per IP)
RATE_LIMIT_MAX = 60
RATE_LIMIT_WINDOW = 60  # seconds


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract Client Info
        forwarded_for = request.headers.get("x-forwarded-for")
        real_ip = request.headers.get("x-real-ip")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        elif real_ip:
            client_ip = real_ip
        else:
            client_ip = request.client.host if request.client else "Unknown"
            
        user_agent = request.headers.get("user-agent", "Unknown")
        path = request.url.path
        method = request.method
        
        # 1. Check Existing Blocklist (Redis)
        if await is_client_blocked(client_ip, user_agent):
            return JSONResponse(status_code=403, content={"detail": "Access Denied: You are blocked."})

        # Get Security Settings
        settings = await get_system_settings()
        sec_config = settings.get("security", {})
        
        # 2. Unusual HTTP Method Blocking (Auto-Ban)
        if method not in ALLOWED_METHODS:
            await add_block(client_ip, "ip", f"Auto-Ban: Unusual method {method}")
            print(f"🚨 AUTO-BANNED {client_ip} for using method {method}")
            return JSONResponse(status_code=405, content={"detail": "Method not allowed."})

        # 3. Strict Anti-Scan (Auto-Ban)
        if sec_config.get("enable_strict_anti_scan", True):
            for bad_path in SENSITIVE_PATHS:
                if bad_path in path:
                    await add_block(client_ip, "ip", f"Auto-Ban: Scanning {path}")
                    print(f"🚨 AUTO-BANNED {client_ip} for accessing {path}")
                    return JSONResponse(status_code=403, content={"detail": "Security Alert: IP Banned."})

        # 4. Honeypot Paths (Auto-Ban)
        if sec_config.get("enable_strict_anti_scan", True):
            for trap in HONEYPOT_PATHS:
                if path.lower().startswith(trap):
                    await add_block(client_ip, "ip", f"Auto-Ban: Honeypot {path}")
                    print(f"🍯 HONEYPOT BANNED {client_ip} for accessing {path}")
                    return JSONResponse(status_code=403, content={"detail": "Security Alert: IP Banned."})

        # 5. Rate-Based Auto-Ban (Flood Detection)
        rate_key = f"rate:{client_ip}"
        try:
            count = await redis_client.incr(rate_key)
            if count == 1:
                await redis_client.expire(rate_key, RATE_LIMIT_WINDOW)
            if count > RATE_LIMIT_MAX:
                await add_block(client_ip, "ip", f"Auto-Ban: Flood ({count} req/{RATE_LIMIT_WINDOW}s)")
                print(f"🌊 FLOOD-BANNED {client_ip} after {count} requests in {RATE_LIMIT_WINDOW}s")
                return JSONResponse(status_code=429, content={"detail": "Too many requests. IP Banned."})
        except Exception:
            pass  # Don't block on Redis failure

        # 6. Country Block
        if sec_config.get("enable_country_block", False):
            allowed = sec_config.get("allowed_countries", [])
            if allowed:
                cc = await get_country_code(client_ip)
                if cc and cc not in allowed:
                    return JSONResponse(status_code=403, content={"detail": "Access Denied: Country not allowed."})

        # 7. User-Agent Block
        if sec_config.get("enable_ua_block", False):
            required = sec_config.get("required_ua_keywords", [])
            if required:
                ua_valid = any(req.lower() in user_agent.lower() for req in required)
                if not ua_valid:
                    return JSONResponse(status_code=403, content={"detail": "Access Denied: Device not supported."})
            
        return await call_next(request)

class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Process request
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            status_code = 500
            raise e
        finally:
            process_time = (time.time() - start_time) * 1000
            
            # Extract details
            # FIX: Handle Reverse Proxy (Nginx/Docker) IPs
            forwarded_for = request.headers.get("x-forwarded-for")
            real_ip = request.headers.get("x-real-ip")
            
            if forwarded_for:
                # X-Forwarded-For: <client>, <proxy1>, <proxy2>
                client_ip = forwarded_for.split(",")[0].strip()
            elif real_ip:
                client_ip = real_ip
            else:
                client_ip = request.client.host if request.client else "Unknown"
            
            method = request.method
            path = request.url.path
            query = request.url.query
            user_agent = request.headers.get("user-agent", "Unknown")
            
            # Filter out health checks to avoid noise
            if path not in ["/metrics", "/favicon.ico"]:
                await log_request_access({
                    "timestamp": datetime.datetime.utcnow(),
                    "ip": client_ip,
                    "method": method,
                    "path": path,
                    "query": query,
                    "status_code": status_code,
                    "duration_ms": round(process_time, 2),
                    "user_agent": user_agent
                })
        
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security-hardening headers to every response."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # --- Always-on headers (safe for both HTML and API) ---
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        
        # --- HSTS: Only when behind HTTPS (detected via reverse proxy header) ---
        proto = request.headers.get("x-forwarded-proto", "http")
        if proto == "https":
            # 1 year, include subdomains
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        # --- CSP: Only for HTML pages (ignore API JSON responses) ---
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
                "img-src 'self' data: https:; "
                "connect-src 'self'"
            )
        
        return response
