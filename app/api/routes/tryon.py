from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.tryon_job import TryOnJob
from app.schemas.common import TryOnJobRead
from app.services.catvton_runtime import get_catvton_runtime_status, request_catvton_warmup
from app.services.auth import get_current_user, get_or_create_guest_user
from app.services.tryon import create_garment_asset, create_tryon_job, save_upload_file
from app.services.vton import run_tryon_job

router = APIRouter()


@router.post("/warmup")
def warmup_tryon() -> dict:
    if settings.TRYON_PROVIDER == "fashn_api":
        return {
            "status": "ready",
            "provider": settings.TRYON_PROVIDER,
            "ready": bool(settings.FASHN_API_KEY.strip()),
            "loading": False,
            "detail": "FASHN runs remotely and does not require local warmup.",
        }
    if settings.TRYON_PROVIDER != "catvton":
        return {
            "status": "unsupported",
            "provider": settings.TRYON_PROVIDER,
            "ready": False,
            "loading": False,
            "detail": "Warmup endpoint is available only for the catvton provider.",
        }
    return request_catvton_warmup()


@router.get("/warmup")
def get_warmup_status() -> dict:
    if settings.TRYON_PROVIDER == "fashn_api":
        return {
            "status": "ready" if settings.FASHN_API_KEY.strip() else "failed",
            "provider": settings.TRYON_PROVIDER,
            "ready": bool(settings.FASHN_API_KEY.strip()),
            "loading": False,
            "detail": None if settings.FASHN_API_KEY.strip() else "FASHN_API_KEY is not configured.",
        }
    if settings.TRYON_PROVIDER != "catvton":
        return {
            "status": "unsupported",
            "provider": settings.TRYON_PROVIDER,
            "ready": False,
            "loading": False,
            "detail": "Warmup status is available only for the catvton provider.",
        }

    runtime_status = get_catvton_runtime_status()
    if runtime_status.get("loaded"):
        status_value = "ready"
    elif runtime_status.get("loading"):
        status_value = "warming"
    elif runtime_status.get("load_error"):
        status_value = "failed"
    else:
        status_value = "idle"

    return {
        "status": status_value,
        "provider": settings.TRYON_PROVIDER,
        "ready": bool(runtime_status.get("loaded")),
        "loading": bool(runtime_status.get("loading")),
        "detail": runtime_status.get("load_error"),
    }


async def _create_job_for_user(
    *,
    db: Session,
    current_user,
    person_image: UploadFile,
    upper_garment_image: Optional[UploadFile],
    lower_garment_image: Optional[UploadFile],
    upper_brand_id: Optional[str],
    lower_brand_id: Optional[str],
    upper_category_code: Optional[str],
    lower_category_code: Optional[str],
) -> TryOnJobRead:
    if not upper_garment_image and not lower_garment_image:
        raise HTTPException(status_code=400, detail="At least one garment image is required.")

    person_path = await save_upload_file(person_image, settings.person_upload_dir, "person")

    upper_asset = None
    lower_asset = None
    if upper_garment_image:
        upper_path = await save_upload_file(upper_garment_image, settings.garment_upload_dir, "upper")
        upper_asset = create_garment_asset(db, current_user, upper_path, upper_brand_id, upper_category_code)
    if lower_garment_image:
        lower_path = await save_upload_file(lower_garment_image, settings.garment_upload_dir, "lower")
        lower_asset = create_garment_asset(db, current_user, lower_path, lower_brand_id, lower_category_code)

    db.commit()

    job = create_tryon_job(db, current_user, person_path, upper_asset, lower_asset)
    return run_tryon_job(db, job)


@router.post("/jobs", response_model=TryOnJobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    person_image: UploadFile = File(...),
    upper_garment_image: Optional[UploadFile] = File(None),
    lower_garment_image: Optional[UploadFile] = File(None),
    upper_brand_id: Optional[str] = Form(None),
    lower_brand_id: Optional[str] = Form(None),
    upper_category_code: Optional[str] = Form(None),
    lower_category_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> TryOnJobRead:
    return await _create_job_for_user(
        db=db,
        current_user=current_user,
        person_image=person_image,
        upper_garment_image=upper_garment_image,
        lower_garment_image=lower_garment_image,
        upper_brand_id=upper_brand_id,
        lower_brand_id=lower_brand_id,
        upper_category_code=upper_category_code,
        lower_category_code=lower_category_code,
    )


@router.post("/guest/jobs", response_model=TryOnJobRead, status_code=status.HTTP_201_CREATED)
async def create_guest_job(
    person_image: UploadFile = File(...),
    upper_garment_image: Optional[UploadFile] = File(None),
    lower_garment_image: Optional[UploadFile] = File(None),
    upper_brand_id: Optional[str] = Form(None),
    lower_brand_id: Optional[str] = Form(None),
    upper_category_code: Optional[str] = Form(None),
    lower_category_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> TryOnJobRead:
    guest_user = get_or_create_guest_user(db)
    return await _create_job_for_user(
        db=db,
        current_user=guest_user,
        person_image=person_image,
        upper_garment_image=upper_garment_image,
        lower_garment_image=lower_garment_image,
        upper_brand_id=upper_brand_id,
        lower_brand_id=lower_brand_id,
        upper_category_code=upper_category_code,
        lower_category_code=lower_category_code,
    )


@router.get("/jobs/{job_id}", response_model=TryOnJobRead)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> TryOnJobRead:
    job = db.query(TryOnJob).filter(TryOnJob.id == job_id, TryOnJob.user_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Try-on job not found.")
    return job


@router.get("/jobs/{job_id}/result")
def get_job_result(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    job = db.query(TryOnJob).filter(TryOnJob.id == job_id, TryOnJob.user_id == current_user.id).first()
    if not job or not job.result_image_url:
        raise HTTPException(status_code=404, detail="Try-on result not available.")
    if not Path(job.result_image_url).exists():
        raise HTTPException(status_code=404, detail="Try-on result file is missing.")
    return FileResponse(path=job.result_image_url)


@router.get("/guest/jobs/{job_id}", response_model=TryOnJobRead)
def get_guest_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> TryOnJobRead:
    guest_user = get_or_create_guest_user(db)
    job = db.query(TryOnJob).filter(TryOnJob.id == job_id, TryOnJob.user_id == guest_user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Guest try-on job not found.")
    return job


@router.get("/guest/jobs/{job_id}/result")
def get_guest_job_result(
    job_id: str,
    db: Session = Depends(get_db),
):
    guest_user = get_or_create_guest_user(db)
    job = db.query(TryOnJob).filter(TryOnJob.id == job_id, TryOnJob.user_id == guest_user.id).first()
    if not job or not job.result_image_url:
        raise HTTPException(status_code=404, detail="Guest try-on result not available.")
    if not Path(job.result_image_url).exists():
        raise HTTPException(status_code=404, detail="Guest try-on result file is missing.")
    return FileResponse(path=job.result_image_url)
