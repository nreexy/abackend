from fastapi import APIRouter, Request, Response
from starlette.responses import RedirectResponse
import json

from app.database import (
    get_dashboard_stats, 
    get_detailed_stats, 
    get_traffic_stats,
    get_collection_names,
    execute_admin_query
)
from .utils import templates, check_ui_auth

router = APIRouter()

# --- DASHBOARD & STATS ---

@router.get("/dashboard")
async def dashboard(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    stats = await get_dashboard_stats()
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": stats, "active_page": "dashboard"})

@router.get("/analytics")
async def view_analytics(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    traffic_data = await get_traffic_stats()
    performance_data = await get_detailed_stats()
    
    return templates.TemplateResponse("analytics.html", {
        "request": request, 
        "traffic": traffic_data, 
        "performance": performance_data,
        "active_page": "analytics"
    })

@router.get("/stats")
async def view_stats_redirect(request: Request):
    return RedirectResponse("/analytics")

@router.get("/details")
async def view_details_redirect(request: Request):
    return RedirectResponse("/analytics#performance")

# --- DOCUMENTATION ---

@router.get("/documentation")
async def view_documentation(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    return templates.TemplateResponse("documentation.html", {
        "request": request, 
        "active_page": "documentation"
    })

# --- DATABASE EXPLORER ---

@router.get("/database")
async def view_database_explorer(request: Request):
    if not await check_ui_auth(request): return RedirectResponse("/login")
    
    collections = await get_collection_names()
    collections.sort()
    
    return templates.TemplateResponse("database.html", {
        "request": request, 
        "active_page": "database",
        "collections": collections
    })

@router.post("/database/query")
async def query_database_action(request: Request):
    if not await check_ui_auth(request): 
        return Response(status_code=401, content="Unauthorized")
        
    try:
        data = await request.json()
        collection = data.get("collection")
        query_str = data.get("query", "{}")
        
        if not collection: return Response(status_code=400, content="Missing collection")
        
        # Parse JSON Query safely
        try:
            query_dict = json.loads(query_str)
        except json.JSONDecodeError:
            return Response(status_code=400, content="Invalid JSON Query")
            
        result = await execute_admin_query(collection, query_dict)
        return result
        
    except Exception as e:
        return Response(status_code=500, content=str(e))
