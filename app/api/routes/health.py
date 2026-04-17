from fastapi import APIRouter

from app.core.config import settings
from app.services.catvton_runtime import get_catvton_runtime_status

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    payload = {
        "status": "ok",
        "project": settings.PROJECT_NAME,
        "environment": settings.APP_ENV,
        "tryon_provider": settings.TRYON_PROVIDER,
        "email_delivery_mode": settings.email_delivery_mode_normalized,
        "email_ready": settings.email_ready,
    }
    if settings.TRYON_PROVIDER == "catvton":
        payload["tryon_runtime"] = get_catvton_runtime_status()
    return payload
