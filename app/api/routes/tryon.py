import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from httpx import HTTPStatusError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.tryon_job import TryOnJob
from app.schemas.common import OperationPerformanceRead, TryOnJobRead, TryOnVideoJobRead, TryOnVideoRead
from app.services.catvton_runtime import get_catvton_runtime_status, request_catvton_warmup
from app.services.auth import get_current_user, get_or_create_guest_user
from app.services.tryon import create_garment_asset, create_tryon_job, save_upload_file_with_metrics
from app.services.video_jobs import owner_key_for_user, schedule_video_job, video_job_store
from app.services.vton import InvalidTryOnImageError, generate_video_from_result_image_with_metrics, run_tryon_job_with_metrics

router = APIRouter()
logger = logging.getLogger(__name__)


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
    user_category: Optional[str],
    upper_category_code: Optional[str],
    lower_category_code: Optional[str],
    upper_garment_photo_type: Optional[str],
    lower_garment_photo_type: Optional[str],
    generation_tier: Optional[str],
) -> TryOnJobRead:
    if not upper_garment_image and not lower_garment_image:
        raise HTTPException(status_code=400, detail="At least one garment image is required.")

    normalized_user_category = (user_category or "").strip().lower() or None
    if upper_garment_image and not upper_category_code:
        upper_category_code = normalized_user_category
    if lower_garment_image and not lower_category_code and not upper_garment_image:
        lower_category_code = normalized_user_category
    if upper_garment_image and not upper_category_code:
        raise HTTPException(status_code=400, detail="Category is required")
    if lower_garment_image and not lower_category_code:
        raise HTTPException(status_code=400, detail="Category is required")

    request_started = time.monotonic()
    person_upload = await save_upload_file_with_metrics(person_image, settings.person_upload_dir, "person")
    person_path = person_upload.path
    logger.info(
        "tryon-input role=person filename=%s type=%s bytes=%s size=%sx%s path=%s",
        person_upload.original_filename,
        person_upload.content_type,
        person_upload.upload_bytes,
        person_upload.image_width,
        person_upload.image_height,
        person_upload.path,
    )

    upper_asset = None
    lower_asset = None
    upload_bytes = person_upload.upload_bytes
    read_ms = person_upload.read_ms
    decode_ms = person_upload.decode_ms
    write_ms = person_upload.write_ms
    if upper_garment_image:
        upper_upload = await save_upload_file_with_metrics(upper_garment_image, settings.garment_upload_dir, "upper")
        logger.info(
            "tryon-input role=upper filename=%s type=%s bytes=%s size=%sx%s category=%s garment_photo_type=%s path=%s",
            upper_upload.original_filename,
            upper_upload.content_type,
            upper_upload.upload_bytes,
            upper_upload.image_width,
            upper_upload.image_height,
            upper_category_code,
            upper_garment_photo_type,
            upper_upload.path,
        )
        upload_bytes += upper_upload.upload_bytes
        read_ms += upper_upload.read_ms
        decode_ms += upper_upload.decode_ms
        write_ms += upper_upload.write_ms
        upper_asset = create_garment_asset(
            db,
            current_user,
            upper_upload.path,
            upper_brand_id,
            upper_category_code,
        )
    if lower_garment_image:
        lower_upload = await save_upload_file_with_metrics(lower_garment_image, settings.garment_upload_dir, "lower")
        logger.info(
            "tryon-input role=lower filename=%s type=%s bytes=%s size=%sx%s category=%s garment_photo_type=%s path=%s",
            lower_upload.original_filename,
            lower_upload.content_type,
            lower_upload.upload_bytes,
            lower_upload.image_width,
            lower_upload.image_height,
            lower_category_code,
            lower_garment_photo_type,
            lower_upload.path,
        )
        upload_bytes += lower_upload.upload_bytes
        read_ms += lower_upload.read_ms
        decode_ms += lower_upload.decode_ms
        write_ms += lower_upload.write_ms
        lower_asset = create_garment_asset(
            db,
            current_user,
            lower_upload.path,
            lower_brand_id,
            lower_category_code,
        )

    db.commit()

    job = create_tryon_job(db, current_user, person_path, upper_asset, lower_asset)
    try:
        job, runtime_performance, routing_metadata = run_tryon_job_with_metrics(
            db,
            job,
            upper_category_code=upper_category_code,
            lower_category_code=lower_category_code,
            upper_garment_photo_type=upper_garment_photo_type,
            lower_garment_photo_type=lower_garment_photo_type,
            generation_tier=generation_tier,
        )
    except InvalidTryOnImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPStatusError as exc:
        response_text = exc.response.text[:1000] if exc.response is not None else ""
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FASHN request failed with HTTP {exc.response.status_code if exc.response else 502}: {response_text}",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    total_ms = int((time.monotonic() - request_started) * 1000)
    performance = OperationPerformanceRead(
        upload_bytes=upload_bytes,
        image_width=person_upload.image_width,
        image_height=person_upload.image_height,
        read_ms=read_ms,
        decode_ms=decode_ms,
        write_ms=write_ms,
        processing_ms=runtime_performance.processing_ms,
        finalize_ms=runtime_performance.finalize_ms,
        response_ms=total_ms,
        total_ms=total_ms,
    )
    logger.info(
        "tryon request user=%s bytes=%s read_ms=%s decode_ms=%s write_ms=%s processing_ms=%s total_ms=%s",
        current_user.id,
        upload_bytes,
        read_ms,
        decode_ms,
        write_ms,
        runtime_performance.processing_ms,
        total_ms,
    )
    payload = TryOnJobRead.model_validate(job)
    return payload.model_copy(
        update={
            "performance": performance,
            "requested_generation_tier": routing_metadata.requested_generation_tier if routing_metadata else None,
            "final_generation_tier": routing_metadata.final_generation_tier if routing_metadata else None,
            "fashn_model_used": routing_metadata.fashn_model_used if routing_metadata else None,
            "credits_charged": routing_metadata.credits_charged if routing_metadata else None,
            "openai_analysis_success": routing_metadata.openai_analysis_success if routing_metadata else None,
            "fallback_used": routing_metadata.fallback_used if routing_metadata else None,
            "forced_premium": routing_metadata.forced_premium if routing_metadata else None,
            "premium_recommended": routing_metadata.premium_recommended if routing_metadata else None,
            "fashn_job_id": routing_metadata.fashn_job_id if routing_metadata else None,
        }
    )


async def _create_video_response(
    *,
    result_image: UploadFile,
    prompt: Optional[str],
    duration_seconds: int,
    resolution: str,
) -> TryOnVideoRead:
    request_started = time.monotonic()
    upload = await save_upload_file_with_metrics(result_image, settings.tryon_result_dir, "video_source")
    result_image_path = upload.path

    try:
        outcome = generate_video_from_result_image_with_metrics(
            image_path=result_image_path,
            prompt=prompt,
            duration_seconds=duration_seconds,
            resolution=resolution,
        )
    except HTTPStatusError as exc:
        response_text = exc.response.text[:1000] if exc.response is not None else ""
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FASHN video request failed with HTTP {exc.response.status_code if exc.response else 502}: {response_text}",
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    total_ms = int((time.monotonic() - request_started) * 1000)
    performance = OperationPerformanceRead(
        upload_bytes=upload.upload_bytes,
        image_width=upload.image_width,
        image_height=upload.image_height,
        read_ms=upload.read_ms,
        decode_ms=upload.decode_ms,
        write_ms=upload.write_ms,
        processing_ms=outcome.performance.processing_ms,
        finalize_ms=outcome.performance.finalize_ms,
        provider_submit_ms=outcome.performance.provider_submit_ms,
        provider_poll_ms=outcome.performance.provider_poll_ms,
        response_ms=total_ms,
        total_ms=total_ms,
        poll_count=outcome.performance.poll_count,
    )
    logger.info(
        "video-sync bytes=%s read_ms=%s decode_ms=%s write_ms=%s provider_submit_ms=%s provider_poll_ms=%s total_ms=%s",
        upload.upload_bytes,
        upload.read_ms,
        upload.decode_ms,
        upload.write_ms,
        outcome.performance.provider_submit_ms,
        outcome.performance.provider_poll_ms,
        total_ms,
    )
    return TryOnVideoRead(
        video_url=outcome.video_url,
        duration_seconds=duration_seconds,
        resolution=resolution,
        provider=settings.TRYON_PROVIDER,
        performance=performance,
    )


@router.post("/jobs", response_model=TryOnJobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    person_image: UploadFile = File(...),
    upper_garment_image: Optional[UploadFile] = File(None),
    lower_garment_image: Optional[UploadFile] = File(None),
    upper_brand_id: Optional[str] = Form(None),
    lower_brand_id: Optional[str] = Form(None),
    user_category: Optional[str] = Form(None),
    upper_category_code: Optional[str] = Form(None),
    lower_category_code: Optional[str] = Form(None),
    upper_garment_photo_type: Optional[str] = Form(None),
    lower_garment_photo_type: Optional[str] = Form(None),
    generation_tier: Optional[str] = Form(None),
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
        user_category=user_category,
        upper_category_code=upper_category_code,
        lower_category_code=lower_category_code,
        upper_garment_photo_type=upper_garment_photo_type,
        lower_garment_photo_type=lower_garment_photo_type,
        generation_tier=generation_tier,
    )


@router.post("/guest/jobs", response_model=TryOnJobRead, status_code=status.HTTP_201_CREATED)
async def create_guest_job(
    person_image: UploadFile = File(...),
    upper_garment_image: Optional[UploadFile] = File(None),
    lower_garment_image: Optional[UploadFile] = File(None),
    upper_brand_id: Optional[str] = Form(None),
    lower_brand_id: Optional[str] = Form(None),
    user_category: Optional[str] = Form(None),
    upper_category_code: Optional[str] = Form(None),
    lower_category_code: Optional[str] = Form(None),
    upper_garment_photo_type: Optional[str] = Form(None),
    lower_garment_photo_type: Optional[str] = Form(None),
    generation_tier: Optional[str] = Form(None),
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
        user_category=user_category,
        upper_category_code=upper_category_code,
        lower_category_code=lower_category_code,
        upper_garment_photo_type=upper_garment_photo_type,
        lower_garment_photo_type=lower_garment_photo_type,
        generation_tier=generation_tier,
    )


@router.post("/video/jobs", response_model=TryOnVideoJobRead, status_code=status.HTTP_201_CREATED)
async def create_tryon_video_job(
    result_image: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    duration_seconds: int = Form(5),
    resolution: str = Form("480p"),
    current_user=Depends(get_current_user),
) -> TryOnVideoJobRead:
    return await _create_video_job_response(
        owner_key=owner_key_for_user(current_user.id),
        result_image=result_image,
        prompt=prompt,
        duration_seconds=duration_seconds,
        resolution=resolution,
    )


@router.get("/video/jobs/{job_id}", response_model=TryOnVideoJobRead)
def get_tryon_video_job(
    job_id: str,
    current_user=Depends(get_current_user),
) -> TryOnVideoJobRead:
    record = video_job_store.get_job(job_id, owner_key_for_user(current_user.id))
    if not record:
        raise HTTPException(status_code=404, detail="Try-on video job not found.")
    return _video_job_payload(record)


@router.post("/guest/video/jobs", response_model=TryOnVideoJobRead, status_code=status.HTTP_201_CREATED)
async def create_guest_tryon_video_job(
    result_image: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    duration_seconds: int = Form(5),
    resolution: str = Form("480p"),
    db: Session = Depends(get_db),
) -> TryOnVideoJobRead:
    guest_user = get_or_create_guest_user(db)
    return await _create_video_job_response(
        owner_key=owner_key_for_user(guest_user.id),
        result_image=result_image,
        prompt=prompt,
        duration_seconds=duration_seconds,
        resolution=resolution,
    )


@router.get("/guest/video/jobs/{job_id}", response_model=TryOnVideoJobRead)
def get_guest_tryon_video_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> TryOnVideoJobRead:
    guest_user = get_or_create_guest_user(db)
    record = video_job_store.get_job(job_id, owner_key_for_user(guest_user.id))
    if not record:
        raise HTTPException(status_code=404, detail="Guest try-on video job not found.")
    return _video_job_payload(record)


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


def _video_job_payload(record) -> TryOnVideoJobRead:
    return TryOnVideoJobRead(
        id=record.id,
        status=record.status,
        status_message=record.status_message,
        video_url=record.video_url,
        error_message=record.error_message,
        duration_seconds=record.duration_seconds,
        resolution=record.resolution,
        provider=record.provider,
        performance=OperationPerformanceRead(**record.performance) if record.performance else None,
    )


async def _create_video_job_response(
    *,
    owner_key: str,
    result_image: UploadFile,
    prompt: Optional[str],
    duration_seconds: int,
    resolution: str,
) -> TryOnVideoJobRead:
    upload = await save_upload_file_with_metrics(result_image, settings.tryon_result_dir, "video_source")
    record = video_job_store.create_job(
        owner_key=owner_key,
        image_path=upload.path,
        prompt=prompt,
        duration_seconds=duration_seconds,
        resolution=resolution,
    )
    record = video_job_store.update_job(
        record.id,
        performance=OperationPerformanceRead(
            upload_bytes=upload.upload_bytes,
            image_width=upload.image_width,
            image_height=upload.image_height,
            read_ms=upload.read_ms,
            decode_ms=upload.decode_ms,
            write_ms=upload.write_ms,
        ).model_dump(),
    ) or record
    logger.info(
        "video-job-created owner=%s job=%s bytes=%s read_ms=%s decode_ms=%s write_ms=%s",
        owner_key,
        record.id,
        upload.upload_bytes,
        upload.read_ms,
        upload.decode_ms,
        upload.write_ms,
    )
    schedule_video_job(record)
    return _video_job_payload(record)


@router.post("/video", response_model=TryOnVideoRead)
async def create_tryon_video(
    result_image: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    duration_seconds: int = Form(5),
    resolution: str = Form("480p"),
    current_user=Depends(get_current_user),
) -> TryOnVideoRead:
    _ = current_user
    return await _create_video_response(
        result_image=result_image,
        prompt=prompt,
        duration_seconds=duration_seconds,
        resolution=resolution,
    )


@router.post("/guest/video", response_model=TryOnVideoRead)
async def create_guest_tryon_video(
    result_image: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    duration_seconds: int = Form(5),
    resolution: str = Form("480p"),
) -> TryOnVideoRead:
    return await _create_video_response(
        result_image=result_image,
        prompt=prompt,
        duration_seconds=duration_seconds,
        resolution=resolution,
    )
