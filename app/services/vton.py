from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

import httpx
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.garment_asset import GarmentAsset
from app.models.tryon_job import TryOnJob
from app.services.catvton_runtime import get_catvton_runtime
from app.services.garment_prompt_service import generate_premium_garment_prompt
from app.services.tryon_routing import GenerationTier, resolve_tryon_routing

logger = logging.getLogger(__name__)

JPEG_DATA_URL_PREFIX = "data:image/jpeg;base64,"
MAX_NORMALIZED_DIMENSION = 768
MIN_IMAGE_BASE64_LENGTH = 1024
MIN_IMAGE_DIMENSION = 160
MAX_IMAGE_ASPECT_RATIO = 4.0
MAX_BACKGROUND_RATIO = 0.94
MIN_FOREGROUND_RATIO = 0.08


class InvalidTryOnImageError(ValueError):
    pass


@dataclass
class TryOnRoutingMetadata:
    requested_generation_tier: str
    final_generation_tier: str
    fashn_model_used: str
    credits_charged: int
    openai_analysis_success: bool
    fallback_used: bool
    forced_premium: bool
    premium_recommended: bool
    category: str
    fashn_job_id: str | None = None
    generated_prompt: str | None = None


@dataclass
class TryOnProviderOutcome:
    result_path: Path
    metadata: TryOnRoutingMetadata | None = None


@dataclass
class OperationPerformance:
    upload_bytes: int | None = None
    image_width: int | None = None
    image_height: int | None = None
    read_ms: int | None = None
    decode_ms: int | None = None
    write_ms: int | None = None
    processing_ms: int | None = None
    finalize_ms: int | None = None
    provider_submit_ms: int | None = None
    provider_poll_ms: int | None = None
    response_ms: int | None = None
    download_ms: int | None = None
    total_ms: int | None = None
    poll_count: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "upload_bytes": self.upload_bytes,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "read_ms": self.read_ms,
            "decode_ms": self.decode_ms,
            "write_ms": self.write_ms,
            "processing_ms": self.processing_ms,
            "finalize_ms": self.finalize_ms,
            "provider_submit_ms": self.provider_submit_ms,
            "provider_poll_ms": self.provider_poll_ms,
            "response_ms": self.response_ms,
            "download_ms": self.download_ms,
            "total_ms": self.total_ms,
            "poll_count": self.poll_count,
        }


@dataclass
class VideoGenerationOutcome:
    video_url: str
    performance: OperationPerformance


def run_tryon_job(db: Session, job: TryOnJob) -> TryOnJob:
    processed_job, _, _ = run_tryon_job_with_metrics(db, job)
    return processed_job


def run_tryon_job_with_metrics(
    db: Session,
    job: TryOnJob,
    *,
    upper_category_code: Optional[str] = None,
    lower_category_code: Optional[str] = None,
    upper_garment_photo_type: Optional[str] = None,
    lower_garment_photo_type: Optional[str] = None,
    generation_tier: Optional[str] = None,
) -> tuple[TryOnJob, OperationPerformance, TryOnRoutingMetadata | None]:
    upper_asset = _get_asset(db, job.upper_garment_asset_id)
    lower_asset = _get_asset(db, job.lower_garment_asset_id)
    operation_started = time.monotonic()
    performance = OperationPerformance()
    routing_metadata: TryOnRoutingMetadata | None = None

    try:
        if settings.TRYON_PROVIDER == "stub":
            provider_outcome = TryOnProviderOutcome(result_path=_run_stub(job, upper_asset, lower_asset, db))
        elif settings.TRYON_PROVIDER == "fashn_api":
            provider_outcome = _run_fashn_two_pass(
                job,
                upper_asset,
                lower_asset,
                db,
                upper_category_code=upper_category_code,
                lower_category_code=lower_category_code,
                upper_garment_photo_type=upper_garment_photo_type,
                lower_garment_photo_type=lower_garment_photo_type,
                requested_generation_tier=generation_tier,
            )
        elif settings.TRYON_PROVIDER == "catvton":
            provider_outcome = TryOnProviderOutcome(
                result_path=_run_catvton_two_pass(
                    job,
                    upper_asset,
                    lower_asset,
                    db,
                    upper_category_code=upper_category_code,
                    lower_category_code=lower_category_code,
                )
            )
        elif settings.TRYON_PROVIDER == "command":
            provider_outcome = TryOnProviderOutcome(
                result_path=_run_command_two_pass(
                    job,
                    upper_asset,
                    lower_asset,
                    db,
                    upper_category_code=upper_category_code,
                    lower_category_code=lower_category_code,
                )
            )
        else:
            raise RuntimeError(f"Unsupported TRYON_PROVIDER: {settings.TRYON_PROVIDER}")
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:2000]
        db.commit()
        db.refresh(job)
        raise

    result_path = provider_outcome.result_path
    routing_metadata = provider_outcome.metadata

    performance.processing_ms = int((time.monotonic() - operation_started) * 1000)
    finalize_started = time.monotonic()
    job.result_image_url = str(result_path)
    job.status = "completed"
    job.error_message = None
    db.commit()
    db.refresh(job)
    performance.finalize_ms = int((time.monotonic() - finalize_started) * 1000)
    performance.total_ms = int((time.monotonic() - operation_started) * 1000)
    return job, performance, routing_metadata


def generate_video_from_result_image(
    *,
    image_path: Path,
    prompt: Optional[str] = None,
    duration_seconds: int = 5,
    resolution: str = "480p",
) -> str:
    return generate_video_from_result_image_with_metrics(
        image_path=image_path,
        prompt=prompt,
        duration_seconds=duration_seconds,
        resolution=resolution,
    ).video_url


def generate_video_from_result_image_with_metrics(
    *,
    image_path: Path,
    prompt: Optional[str] = None,
    duration_seconds: int = 5,
    resolution: str = "480p",
    status_callback: Optional[Callable[[str, Optional[str]], None]] = None,
) -> VideoGenerationOutcome:
    if settings.TRYON_PROVIDER != "fashn_api":
        raise RuntimeError("Video generation is currently available only with the fashn_api provider.")

    if not settings.FASHN_API_KEY:
        raise RuntimeError("FASHN_API_KEY is not configured.")

    if duration_seconds not in {5, 10}:
        raise RuntimeError("FASHN image-to-video currently supports only 5s or 10s duration.")

    normalized_resolution = resolution.strip().lower()
    if normalized_resolution not in {"480p", "720p", "1080p"}:
        raise RuntimeError("FASHN image-to-video currently supports only 480p, 720p, or 1080p.")

    started_at = time.monotonic()
    performance = OperationPerformance()
    timeout_seconds = max(settings.TRYON_TIMEOUT_SECONDS, 240)
    headers = {
        "Authorization": f"Bearer {settings.FASHN_API_KEY}",
        "Content-Type": "application/json",
    }
    data_url_started = time.monotonic()
    payload = {
        "model_name": "image-to-video",
        "inputs": {
            "image": _image_to_data_url(image_path),
            "duration": duration_seconds,
            "resolution": normalized_resolution,
        },
    }
    performance.decode_ms = int((time.monotonic() - data_url_started) * 1000)

    if prompt and prompt.strip():
        payload["inputs"]["prompt"] = prompt.strip()

    with httpx.Client(timeout=timeout_seconds) as client:
        if status_callback:
            status_callback("processing", "Generating video")

        provider_submit_started = time.monotonic()
        response = client.post(f"{settings.FASHN_BASE_URL}/run", headers=headers, json=payload)
        response.raise_for_status()
        performance.provider_submit_ms = int((time.monotonic() - provider_submit_started) * 1000)
        prediction_id = response.json().get("id")
        if not prediction_id:
            raise RuntimeError(f"FASHN did not return a video prediction id: {response.text[:1000]}")

        deadline = time.monotonic() + timeout_seconds
        provider_poll_started = time.monotonic()
        poll_count = 0
        while time.monotonic() < deadline:
            status_response = client.get(f"{settings.FASHN_BASE_URL}/status/{prediction_id}", headers=headers)
            status_response.raise_for_status()
            status_payload = status_response.json()
            status_value = status_payload.get("status")
            poll_count += 1
            performance.poll_count = poll_count
            if status_value == "completed":
                performance.provider_poll_ms = int((time.monotonic() - provider_poll_started) * 1000)
                if status_callback:
                    status_callback("finalizing", "Finalizing")
                outputs = status_payload.get("output") or []
                if not outputs:
                    raise RuntimeError("FASHN video prediction completed without output.")
                performance.processing_ms = int((time.monotonic() - started_at) * 1000)
                performance.total_ms = performance.processing_ms
                return VideoGenerationOutcome(video_url=outputs[0], performance=performance)
            if status_value == "failed":
                error_payload = status_payload.get("error") or {}
                raise RuntimeError(
                    "FASHN video prediction failed: "
                    f"{error_payload.get('name', 'UnknownError')} - {error_payload.get('message', 'No details')}"
                )
            if status_callback:
                status_callback("processing", "Generating video")
            time.sleep(settings.TRYON_POLL_INTERVAL_SECONDS)

    raise TimeoutError("FASHN video prediction timed out.")


def _run_stub(job: TryOnJob, upper_asset: Optional[GarmentAsset], lower_asset: Optional[GarmentAsset], db: Session) -> Path:
    current_image = Path(job.person_image_url)
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
    if lower_asset:
        job.status = "processing_lower"
        db.commit()

    result_path = settings.tryon_result_dir / _result_filename(job.id)
    return _finalize_result_file(current_image, result_path)


def _run_fashn_two_pass(
    job: TryOnJob,
    upper_asset: Optional[GarmentAsset],
    lower_asset: Optional[GarmentAsset],
    db: Session,
    *,
    upper_category_code: Optional[str] = None,
    lower_category_code: Optional[str] = None,
    upper_garment_photo_type: Optional[str] = None,
    lower_garment_photo_type: Optional[str] = None,
    requested_generation_tier: Optional[str] = None,
) -> TryOnProviderOutcome:
    if not settings.FASHN_API_KEY:
        raise RuntimeError("FASHN_API_KEY is not configured.")

    current_image_path = Path(job.person_image_url)
    collected_metadata: list[TryOnRoutingMetadata] = []
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
        selected_upper_category_code = _selected_category_code(upper_category_code, upper_asset, "tshirt")
        upper_outcome = _run_fashn_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(upper_asset.image_url),
            category_code=selected_upper_category_code,
            output_path=settings.tryon_result_dir / f"{job.id}_upper_pass.{settings.FASHN_OUTPUT_FORMAT}",
            garment_photo_type=_resolved_garment_photo_type(upper_garment_photo_type, upper_asset),
            debug_label=f"{job.id}_upper_pass",
            requested_generation_tier=requested_generation_tier,
            user_id=job.user_id,
        )
        current_image_path = upper_outcome.result_path
        if upper_outcome.metadata:
            collected_metadata.append(upper_outcome.metadata)

    if lower_asset:
        job.status = "processing_lower"
        db.commit()
        selected_lower_category_code = _selected_category_code(lower_category_code, lower_asset, "pants")
        lower_outcome = _run_fashn_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(lower_asset.image_url),
            category_code=selected_lower_category_code,
            output_path=settings.tryon_result_dir / f"{job.id}_lower_pass.{settings.FASHN_OUTPUT_FORMAT}",
            garment_photo_type=_resolved_garment_photo_type(lower_garment_photo_type, lower_asset),
            debug_label=f"{job.id}_lower_pass",
            requested_generation_tier=requested_generation_tier,
            user_id=job.user_id,
        )
        current_image_path = lower_outcome.result_path
        if lower_outcome.metadata:
            collected_metadata.append(lower_outcome.metadata)

    final_path = settings.tryon_result_dir / _result_filename(job.id)
    return TryOnProviderOutcome(
        result_path=_finalize_result_file(current_image_path, final_path),
        metadata=_aggregate_routing_metadata(collected_metadata),
    )


def _run_command_two_pass(
    job: TryOnJob,
    upper_asset: Optional[GarmentAsset],
    lower_asset: Optional[GarmentAsset],
    db: Session,
    *,
    upper_category_code: Optional[str] = None,
    lower_category_code: Optional[str] = None,
) -> Path:
    current_image_path = Path(job.person_image_url)
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
        current_image_path = _run_command_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(upper_asset.image_url),
            category=_selected_category_code(upper_category_code, upper_asset, "tops"),
            output_path=settings.tryon_result_dir / f"{job.id}_upper_pass.png",
        )

    if lower_asset:
        job.status = "processing_lower"
        db.commit()
        current_image_path = _run_command_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(lower_asset.image_url),
            category=_selected_category_code(lower_category_code, lower_asset, "bottoms"),
            output_path=settings.tryon_result_dir / f"{job.id}_lower_pass.png",
        )

    final_path = settings.tryon_result_dir / _result_filename(job.id)
    return _finalize_result_file(current_image_path, final_path)


def _run_catvton_two_pass(
    job: TryOnJob,
    upper_asset: Optional[GarmentAsset],
    lower_asset: Optional[GarmentAsset],
    db: Session,
    *,
    upper_category_code: Optional[str] = None,
    lower_category_code: Optional[str] = None,
) -> Path:
    current_image_path = Path(job.person_image_url)
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
        current_image_path = _run_catvton_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(upper_asset.image_url),
            category=_selected_category_code(upper_category_code, upper_asset, "upper"),
            output_path=settings.tryon_result_dir / f"{job.id}_upper_pass.png",
        )

    if lower_asset:
        job.status = "processing_lower"
        db.commit()
        current_image_path = _run_catvton_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(lower_asset.image_url),
            category=_selected_category_code(lower_category_code, lower_asset, "lower"),
            output_path=settings.tryon_result_dir / f"{job.id}_lower_pass.png",
        )

    final_path = settings.tryon_result_dir / _result_filename(job.id)
    return _finalize_result_file(current_image_path, final_path)


def _run_fashn_pass(
    model_image_path: Path,
    garment_image_path: Path,
    category_code: str,
    output_path: Path,
    garment_photo_type: str,
    debug_label: str,
    requested_generation_tier: Optional[str],
    user_id: str,
) -> TryOnProviderOutcome:
    headers = {
        "Authorization": f"Bearer {settings.FASHN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload, routing_metadata = _build_fashn_payload(
        model_image_path=model_image_path,
        garment_image_path=garment_image_path,
        category_code=category_code,
        garment_photo_type=garment_photo_type,
        requested_generation_tier=requested_generation_tier,
    )
    debug_directory = _persist_fashn_debug_request(
        debug_label=debug_label,
        model_image_path=model_image_path,
        garment_image_path=garment_image_path,
        payload=payload,
        routing_metadata=routing_metadata,
        selected_garment_photo_type=garment_photo_type,
    )
    model_metadata = _image_metadata(model_image_path)
    garment_metadata = _image_metadata(garment_image_path)
    logger.info(
        "fashn-request label=%s user_id=%s requested_generation_tier=%s final_generation_tier=%s model_name=%s selected_category_code=%s request_category=%s model=%sx%s/%sB garment=%sx%s/%sB",
        debug_label,
        user_id,
        routing_metadata.requested_generation_tier,
        routing_metadata.final_generation_tier,
        payload["model_name"],
        category_code,
        payload.get("inputs", {}).get("category"),
        model_metadata["width"],
        model_metadata["height"],
        model_metadata["bytes"],
        garment_metadata["width"],
        garment_metadata["height"],
        garment_metadata["bytes"],
    )

    with httpx.Client(timeout=settings.TRYON_TIMEOUT_SECONDS) as client:
        response = client.post(f"{settings.FASHN_BASE_URL}/run", headers=headers, json=payload)
        response.raise_for_status()
        prediction_id = response.json().get("id")
        if not prediction_id:
            raise RuntimeError(f"FASHN did not return a prediction id: {response.text[:1000]}")
        routing_metadata.fashn_job_id = prediction_id
        if debug_directory is not None:
            _write_json(debug_directory / "submit_response.json", response.json())
            (debug_directory / "prediction_id.txt").write_text(prediction_id, encoding="utf-8")
        logger.info(
            "tryon-routing-success user_id=%s category=%s requested_generation_tier=%s final_generation_tier=%s fashn_model_used=%s credits_charged=%s openai_analysis_success=%s fallback_used=%s fashn_job_id=%s created_at=%s",
            user_id,
            routing_metadata.category,
            routing_metadata.requested_generation_tier,
            routing_metadata.final_generation_tier,
            routing_metadata.fashn_model_used,
            routing_metadata.credits_charged,
            routing_metadata.openai_analysis_success,
            routing_metadata.fallback_used,
            prediction_id,
            int(time.time()),
        )

        deadline = time.monotonic() + settings.TRYON_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status_response = client.get(f"{settings.FASHN_BASE_URL}/status/{prediction_id}", headers=headers)
            status_response.raise_for_status()
            payload = status_response.json()
            status_value = payload.get("status")
            if status_value == "completed":
                outputs = payload.get("output") or []
                if not outputs:
                    raise RuntimeError("FASHN prediction completed without output.")
                if debug_directory is not None:
                    _write_json(debug_directory / "status_completed.json", payload)
                    (debug_directory / "output_reference.txt").write_text(str(outputs[0]), encoding="utf-8")
                _persist_output(outputs[0], output_path)
                if debug_directory is not None:
                    provider_output_path = debug_directory / f"provider_output{output_path.suffix.lower() or '.img'}"
                    shutil.copyfile(output_path, provider_output_path)
                    _write_json(
                        debug_directory / "provider_output_metadata.json",
                        _image_metadata(output_path),
                    )
                return TryOnProviderOutcome(result_path=output_path, metadata=routing_metadata)
            if status_value == "failed":
                if debug_directory is not None:
                    _write_json(debug_directory / "status_failed.json", payload)
                error_payload = payload.get("error") or {}
                raise RuntimeError(
                    f"FASHN prediction failed: {error_payload.get('name', 'UnknownError')} - {error_payload.get('message', 'No details')}"
                )
            time.sleep(settings.TRYON_POLL_INTERVAL_SECONDS)

    raise TimeoutError("FASHN prediction timed out.")


def _run_command_pass(model_image_path: Path, garment_image_path: Path, category: str, output_path: Path) -> Path:
    template = settings.MODEL_COMMAND_TEMPLATE.strip()
    if not template:
        raise RuntimeError("MODEL_COMMAND_TEMPLATE is not configured for command provider.")

    required_tokens = ("{person}", "{garment}", "{output}")
    missing_tokens = [token for token in required_tokens if token not in template]
    if missing_tokens:
        raise RuntimeError(f"MODEL_COMMAND_TEMPLATE is missing placeholders: {', '.join(missing_tokens)}")

    mapping = {
        "person": shlex.quote(str(model_image_path)),
        "garment": shlex.quote(str(garment_image_path)),
        "output": shlex.quote(str(output_path)),
        "category": shlex.quote(category),
        "steps": settings.TRYON_NUM_INFERENCE_STEPS,
        "guidance": settings.TRYON_GUIDANCE_SCALE,
        "seed": settings.TRYON_SEED,
    }
    command = template.format(**mapping)

    env = os.environ.copy()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    completed = subprocess.run(
        command,
        shell=True,
        executable="/bin/bash",
        cwd=str(settings.runtime_workdir),
        env=env,
        text=True,
        capture_output=True,
        timeout=settings.TRYON_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Command provider failed ({completed.returncode}): {stderr[:2000]}")

    if not output_path.exists():
        raise RuntimeError("Command provider did not generate the output image.")
    return output_path


def _run_catvton_pass(model_image_path: Path, garment_image_path: Path, category: str, output_path: Path) -> Path:
    runtime = get_catvton_runtime()
    return runtime.predict(
        person_image_path=model_image_path,
        garment_image_path=garment_image_path,
        category=category,
        output_path=output_path,
        num_inference_steps=settings.TRYON_NUM_INFERENCE_STEPS,
        guidance_scale=settings.TRYON_GUIDANCE_SCALE,
        seed=settings.TRYON_SEED,
    )


def _persist_output(output_value: str, output_path: Path) -> None:
    if output_value.startswith("data:image/"):
        _, encoded = output_value.split(",", 1)
        output_path.write_bytes(base64.b64decode(encoded))
        return

    with httpx.Client(timeout=settings.TRYON_TIMEOUT_SECONDS) as client:
        response = client.get(output_value)
        response.raise_for_status()
        output_path.write_bytes(response.content)


def _result_filename(job_id: str) -> str:
    result_format = settings.TRYON_RESULT_FORMAT.strip().lower()
    if result_format in {"jpg", "jpeg"}:
        return f"{job_id}_result.jpg"
    if result_format == "webp":
        return f"{job_id}_result.webp"
    return f"{job_id}_result.png"


def _finalize_result_file(source_path: Path, final_path: Path) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if final_path.exists():
        final_path.unlink()

    result_format = settings.TRYON_RESULT_FORMAT.strip().lower()
    if _matches_target_format(source_path, result_format):
        if source_path != final_path:
            shutil.copyfile(source_path, final_path)
        return final_path

    with Image.open(source_path) as source_image:
        if result_format == "png":
            source_image.save(final_path, format="PNG")
            return final_path
        image = source_image.convert("RGB")
        if result_format == "webp":
            image.save(final_path, format="WEBP", quality=settings.TRYON_RESULT_JPEG_QUALITY, method=6)
            return final_path

        image.save(
            final_path,
            format="JPEG",
            quality=settings.TRYON_RESULT_JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
    return final_path


def _image_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _persist_fashn_debug_request(
    *,
    debug_label: str,
    model_image_path: Path,
    garment_image_path: Path,
    payload: dict,
    routing_metadata: TryOnRoutingMetadata,
    selected_garment_photo_type: str,
) -> Optional[Path]:
    if not settings.FASHN_DEBUG_SAVE_REQUESTS:
        return None

    try:
        directory = settings.fashn_debug_dir / _sanitize_debug_label(debug_label)
        directory.mkdir(parents=True, exist_ok=True)

        model_copy_path = directory / f"model_input{model_image_path.suffix.lower() or '.img'}"
        garment_copy_path = directory / f"garment_input{garment_image_path.suffix.lower() or '.img'}"
        shutil.copyfile(model_image_path, model_copy_path)
        shutil.copyfile(garment_image_path, garment_copy_path)
        _write_json(directory / "request_payload.json", payload)
        _write_json(
            directory / "request_metadata.json",
            {
                "model_image": _image_metadata(model_image_path),
                "garment_image": _image_metadata(garment_image_path),
                "payload_summary": {
                    "model_name": payload.get("model_name"),
                    "requested_generation_tier": routing_metadata.requested_generation_tier,
                    "final_generation_tier": routing_metadata.final_generation_tier,
                    "selected_category_code": routing_metadata.category,
                    "selected_garment_photo_type": selected_garment_photo_type,
                    "category": payload.get("inputs", {}).get("category"),
                    "credits_charged": routing_metadata.credits_charged,
                    "fashn_model_used": routing_metadata.fashn_model_used,
                    "openai_analysis_success": routing_metadata.openai_analysis_success,
                    "fallback_used": routing_metadata.fallback_used,
                    "forced_premium": routing_metadata.forced_premium,
                    "premium_recommended": routing_metadata.premium_recommended,
                    "generated_prompt": routing_metadata.generated_prompt,
                    "seed": payload.get("inputs", {}).get("seed"),
                    "num_samples": payload.get("inputs", {}).get("num_samples"),
                    "generation_mode": payload.get("inputs", {}).get("generation_mode"),
                    "resolution": payload.get("inputs", {}).get("resolution"),
                    "num_images": payload.get("inputs", {}).get("num_images"),
                },
            },
        )
        return directory
    except Exception as exc:  # pragma: no cover - debug fallback
        logger.warning("Failed to persist FASHN debug bundle for %s: %s", debug_label, exc)
        return None


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _image_metadata(path: Path) -> dict[str, object]:
    metadata: dict[str, object] = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "format": (mimetypes.guess_type(path.name)[0] or "").lower(),
    }
    with Image.open(path) as image:
        metadata.update(
            {
                "width": image.width,
                "height": image.height,
                "pil_format": image.format,
                "mode": image.mode,
            }
        )
    return metadata


def _sanitize_debug_label(label: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in label)


def _matches_target_format(source_path: Path, result_format: str) -> bool:
    suffix = source_path.suffix.lower()
    if result_format == "png":
        return suffix == ".png"
    if result_format in {"jpg", "jpeg"}:
        return suffix in {".jpg", ".jpeg"}
    if result_format == "webp":
        return suffix == ".webp"
    return False


def _get_asset(db: Session, asset_id: Optional[str]) -> Optional[GarmentAsset]:
    if not asset_id:
        return None
    return db.query(GarmentAsset).filter(GarmentAsset.id == asset_id).first()


def _command_category(asset: GarmentAsset, fallback: str) -> str:
    if asset.category and asset.category.code:
        return asset.category.code
    return fallback


def _selected_category_code(explicit_category_code: Optional[str], asset: Optional[GarmentAsset], fallback: str) -> str:
    if explicit_category_code and explicit_category_code.strip():
        return explicit_category_code.strip().lower()
    if asset and asset.category and asset.category.code:
        return asset.category.code.strip().lower()
    return fallback


def _resolved_garment_photo_type(explicit_garment_photo_type: Optional[str], asset: Optional[GarmentAsset]) -> str:
    _ = asset
    if explicit_garment_photo_type and explicit_garment_photo_type.strip():
        return explicit_garment_photo_type.strip().lower()
    return settings.FASHN_GARMENT_PHOTO_TYPE


def _aggregate_routing_metadata(metadata_items: list[TryOnRoutingMetadata]) -> TryOnRoutingMetadata | None:
    if not metadata_items:
        return None

    first = metadata_items[0]
    if len(metadata_items) == 1:
        return first

    return TryOnRoutingMetadata(
        requested_generation_tier=first.requested_generation_tier,
        final_generation_tier=GenerationTier.premium.value
        if any(item.final_generation_tier == GenerationTier.premium.value for item in metadata_items)
        else GenerationTier.standard.value,
        fashn_model_used="multiple",
        credits_charged=sum(item.credits_charged for item in metadata_items),
        openai_analysis_success=all(item.openai_analysis_success for item in metadata_items if item.final_generation_tier == GenerationTier.premium.value),
        fallback_used=any(item.fallback_used for item in metadata_items),
        forced_premium=any(item.forced_premium for item in metadata_items),
        premium_recommended=any(item.premium_recommended for item in metadata_items),
        category="multiple",
        fashn_job_id=metadata_items[-1].fashn_job_id,
        generated_prompt=None,
    )


def _build_fashn_payload(
    *,
    model_image_path: Path,
    garment_image_path: Path,
    category_code: str,
    garment_photo_type: str,
    requested_generation_tier: Optional[str],
) -> tuple[dict, TryOnRoutingMetadata]:
    _ = garment_photo_type
    routing_decision = resolve_tryon_routing(
        user_category=category_code,
        requested_generation_tier=requested_generation_tier,
    )
    model_image = _normalized_image_to_data_url(model_image_path)
    garment_image = _normalized_image_to_data_url(garment_image_path)
    metadata = TryOnRoutingMetadata(
        requested_generation_tier=routing_decision.requested_generation_tier.value,
        final_generation_tier=routing_decision.final_generation_tier.value,
        fashn_model_used=routing_decision.fashn_model_name,
        credits_charged=routing_decision.credits_required,
        openai_analysis_success=False,
        fallback_used=False,
        forced_premium=routing_decision.force_premium,
        premium_recommended=routing_decision.premium_recommended,
        category=routing_decision.user_category,
    )

    payload = {
        "model": routing_decision.fashn_model_name,
        "category": routing_decision.standard_category,
        "model_image": model_image,
        "garment_image": garment_image,
    }
    print("USER CATEGORY:", routing_decision.user_category)
    print("MAPPED CATEGORY:", payload["category"])
    print("MODEL:", payload["model"])
    print("CATEGORY:", payload["category"])
    print("MODEL IMAGE SIZE:", len(model_image))
    print("GARMENT IMAGE SIZE:", len(garment_image))

    if routing_decision.final_generation_tier == GenerationTier.premium:
        prompt_outcome = generate_premium_garment_prompt(garment_image, routing_decision.user_category)
        metadata.openai_analysis_success = prompt_outcome.openai_analysis_success
        metadata.fallback_used = prompt_outcome.fallback_used
        metadata.generated_prompt = prompt_outcome.prompt
        premium_payload = {
            "model_name": routing_decision.fashn_model_name,
            "inputs": {
                "model_image": model_image,
                "product_image": garment_image,
                "prompt": prompt_outcome.prompt,
                "resolution": "1k",
                "generation_mode": "balanced",
                "num_images": 1,
            },
        }
        return premium_payload, metadata

    standard_payload = {
        "model_name": routing_decision.fashn_model_name,
        "inputs": {
            "model_image": model_image,
            "garment_image": garment_image,
            "category": routing_decision.standard_category,
        },
    }
    return standard_payload, metadata


def validate_image(image_base64: str) -> bool:
    try:
        if not image_base64 or len(image_base64.strip()) < MIN_IMAGE_BASE64_LENGTH:
            return False
        image = _open_base64_image(image_base64)
        width, height = image.size
        if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
            return False
        shorter_side = max(1, min(width, height))
        aspect_ratio = max(width, height) / shorter_side
        if aspect_ratio > MAX_IMAGE_ASPECT_RATIO:
            return False
        background_ratio, foreground_ratio = _estimate_image_content(image)
        if background_ratio > MAX_BACKGROUND_RATIO:
            return False
        if foreground_ratio < MIN_FOREGROUND_RATIO:
            return False
        return True
    except Exception:
        return False


def normalize_image(image_base64: str) -> str:
    image = _open_base64_image(image_base64).convert("RGB")
    if max(image.size) > MAX_NORMALIZED_DIMENSION:
        image.thumbnail((MAX_NORMALIZED_DIMENSION, MAX_NORMALIZED_DIMENSION), _resampling_filter())
    output = BytesIO()
    image.save(output, format="JPEG", quality=95, optimize=True)
    return JPEG_DATA_URL_PREFIX + base64.b64encode(output.getvalue()).decode("utf-8")


def _normalized_image_to_data_url(path: Path) -> str:
    raw_image = _image_to_data_url(path)
    if not validate_image(raw_image):
        raise InvalidTryOnImageError("Invalid image. Please upload a clearer photo.")
    normalized_image = normalize_image(raw_image)
    if not validate_image(normalized_image):
        raise InvalidTryOnImageError("Invalid image. Please upload a clearer photo.")
    return normalized_image


def _open_base64_image(image_base64: str) -> Image.Image:
    encoded = image_base64.split(",", 1)[1] if "," in image_base64 else image_base64
    image = Image.open(BytesIO(base64.b64decode(encoded)))
    image.load()
    return ImageOps.exif_transpose(image)


def _estimate_image_content(image: Image.Image) -> tuple[float, float]:
    preview = image.convert("RGB")
    preview.thumbnail((128, 128), _resampling_filter())
    width, height = preview.size
    if width == 0 or height == 0:
        return 1.0, 0.0

    corners = [
        preview.getpixel((0, 0)),
        preview.getpixel((max(width - 1, 0), 0)),
        preview.getpixel((0, max(height - 1, 0))),
        preview.getpixel((max(width - 1, 0), max(height - 1, 0))),
    ]
    background = tuple(sum(channel) // len(corners) for channel in zip(*corners))
    tolerance = 36
    total_pixels = width * height
    similar_pixels = 0
    foreground_pixels = 0
    for pixel in preview.getdata():
        if all(abs(pixel[index] - background[index]) <= tolerance for index in range(3)):
            similar_pixels += 1
        else:
            foreground_pixels += 1
    return similar_pixels / total_pixels, foreground_pixels / total_pixels


def _resampling_filter() -> int:
    if hasattr(Image, "Resampling"):
        return Image.Resampling.LANCZOS
    return Image.LANCZOS
