from fastapi import APIRouter, Request, Form
from starlette.responses import RedirectResponse

from app.database import (
    get_all_lists,
    get_list_by_id,
    delete_list_by_id,
    create_custom_list,
    get_cache,
    get_system_settings,
)
from .utils import templates, check_ui_auth

router = APIRouter()


@router.get("/curation")
async def view_curation(request: Request):
    if not await check_ui_auth(request):
        return RedirectResponse("/login")

    all_lists = await get_all_lists()
    curated = [l for l in all_lists if l.get("type") == "custom"]

    config = await get_system_settings()

    return templates.TemplateResponse("curation.html", {
        "request": request,
        "lists": curated,
        "config": config,
        "active_page": "curation",
    })


@router.post("/curation/create")
async def create_curation_list(request: Request, name: str = Form(...)):
    if not await check_ui_auth(request):
        return RedirectResponse("/login")

    name = name.strip()
    if not name:
        return RedirectResponse("/curation", status_code=303)

    await create_custom_list(name, [])

    # Find the newly created list by name and type to get its _id
    from app.database import lists_collection
    doc = await lists_collection.find_one({"name": name, "type": "custom"}, sort=[("created_at", -1)])
    if doc:
        return RedirectResponse(f"/curation/{doc['_id']}", status_code=303)
    return RedirectResponse("/curation", status_code=303)


@router.get("/curation/{list_id}")
async def view_curation_list(request: Request, list_id: str):
    if not await check_ui_auth(request):
        return RedirectResponse("/login")

    list_obj = await get_list_by_id(list_id)
    if not list_obj or list_obj.get("type") != "custom":
        return RedirectResponse("/curation")

    config = await get_system_settings()

    books = []
    for asin in list_obj.get("asins", []):
        cached = await get_cache(f"book_v7:{asin}")
        if cached:
            cached["authors_str"] = ", ".join(cached.get("authors", []))
            books.append(cached)
        else:
            books.append({"asin": asin, "title": "Not cached", "authors_str": "-"})

    return templates.TemplateResponse("curation.html", {
        "request": request,
        "active_list": list_obj,
        "books": books,
        "config": config,
        "active_page": "curation",
    })


@router.post("/curation/{list_id}/delete")
async def delete_curation_list(request: Request, list_id: str):
    if not await check_ui_auth(request):
        return RedirectResponse("/login")
    await delete_list_by_id(list_id)
    return RedirectResponse("/curation", status_code=303)


@router.post("/curation/{list_id}/rename")
async def rename_curation_list(request: Request, list_id: str, name: str = Form(...)):
    if not await check_ui_auth(request):
        return RedirectResponse("/login")
    from app.database import update_list_name
    await update_list_name(list_id, name.strip())
    return RedirectResponse(f"/curation/{list_id}", status_code=303)
