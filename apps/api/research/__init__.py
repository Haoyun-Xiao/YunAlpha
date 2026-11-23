from fastapi import APIRouter

from workspaces import research_desks
from .report_library import available_items, reports_router


router = APIRouter(prefix="/research", tags=["research"])
router.include_router(reports_router)


@router.get("/modules")
def modules():
    available = bool(available_items())
    return {"items": [{
        "id": "fund-deep-reports",
        "name": "基金深度报告",
        "groups": ["主动基金"],
        "description": "浏览已归档的基金深度研究报告及版本。",
        "status": "available" if available else "unavailable",
        "entry": {"kind": "local", "label": "查看报告列表", "href": "/?view=reports"} if available else None,
    }, *research_desks()]}
