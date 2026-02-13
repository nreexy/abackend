import time
import datetime
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.database import log_request_access

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
