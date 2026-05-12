from fastapi import APIRouter
from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .library import router as library_router
from .settings import router as settings_router
from .utils import check_ui_auth

router = APIRouter()

router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(library_router)
router.include_router(settings_router)
from .nyt import router as nyt_router
router.include_router(nyt_router)
from .curation import router as curation_router
router.include_router(curation_router)
