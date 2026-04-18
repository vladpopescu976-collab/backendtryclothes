from fastapi import APIRouter

from app.core.config import settings
from app.services.catvton_runtime import get_catvton_runtime_status

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    tryon_ready = True
    payload = {
        "status": "ok",
        "project": settings.PROJECT_NAME,
        "environment": settings.APP_ENV,
        "tryon_provider": settings.TRYON_PROVIDER,
        "tryon_ready": True,
        "email_delivery_mode": settings.email_delivery_mode_normalized,
        "email_ready": settings.email_ready,
    }
    if settings.TRYON_PROVIDER == "catvton":
        runtime_status = get_catvton_runtime_status()
        payload["tryon_runtime"] = runtime_status
        tryon_ready = bool(runtime_status.get("loaded")) and not runtime_status.get("load_error")
    elif settings.TRYON_PROVIDER == "fashn_api":
        tryon_ready = bool(settings.FASHN_API_KEY.strip())
        payload["fashn"] = {
            "configured": tryon_ready,
            "model_name": settings.FASHN_MODEL_NAME,
            "garment_photo_type": settings.FASHN_GARMENT_PHOTO_TYPE,
            "output_format": settings.FASHN_OUTPUT_FORMAT,
        }
    payload["tryon_ready"] = tryon_ready
    return payload
