from __future__ import annotations

import base64
import hashlib
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
from app.services.tryon_routing import (
    GenerationTier,
    resolve_tryon_routing,
    validate_premium_category,
    validate_standard_category,
)

logger = logging.getLogger(__name__)

MIN_IMAGE_BASE64_LENGTH = 1024
MIN_IMAGE_DIMENSION = 160
MAX_IMAGE_ASPECT_RATIO = 4.0
MAX_BACKGROUND_RATIO = 0.94
MIN_FOREGROUND_RATIO = 0.08
FASHN_PASSTHROUGH_MIME_TYPES = {"image/jpeg", "image/png"}


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
    normalized_fashn_category: str | None = None
    fashn_job_id: str | None = None
    generated_prompt: str | None = None


@dataclass
class TryOnProviderOutcome:
    result_path: Path
    metadata: TryOnRoutingMetadata | None = None


@dataclass(frozen=True)
class PreparedFashnInputImage:
    data_url: str
    final_bytes: bytes
    original_width: int
    original_height: int
    final_width: int
    final_height: int
    original_file_size: int
    final_file_size: int
    original_mime_type: str
    final_mime_type: str
    original_hash: str
    final_hash: str
    was_resized: bool
    was_cropped: bool
    was_compressed: bool
    was_converted: bool


@dataclass(frozen=True)
class FashnPayloadBuildResult:
    payload: dict
    routing_metadata: TryOnRoutingMetadata
    model_image: PreparedFashnInputImage
    garment_image: PreparedFashnInputImage
    payload_hash: str


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
            generation_id=job.id,
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
            generation_id=job.id,
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
    generation_id: str,
) -> TryOnProviderOutcome:
    headers = {
        "Authorization": f"Bearer {settings.FASHN_API_KEY}",
        "Content-Type": "application/json",
    }
    build_result = _build_fashn_payload(
        model_image_path=model_image_path,
        garment_image_path=garment_image_path,
        category_code=category_code,
        garment_photo_type=garment_photo_type,
        requested_generation_tier=requested_generation_tier,
    )
    payload = build_result.payload
    routing_metadata = build_result.routing_metadata
    debug_directory = _persist_fashn_debug_request(
        debug_label=debug_label,
        model_image_path=model_image_path,
        garment_image_path=garment_image_path,
        payload=payload,
        payload_hash=build_result.payload_hash,
        routing_metadata=routing_metadata,
        selected_garment_photo_type=garment_photo_type,
        prepared_model_image=build_result.model_image,
        prepared_garment_image=build_result.garment_image,
    )
    logger.info(
        "FASHN_DEBUG generation_id=%s user_id=%s requested_category=%s final_category=%s model_name=%s requested_generation_tier=%s final_generation_tier=%s",
        generation_id,
        user_id,
        category_code,
        routing_metadata.normalized_fashn_category,
        payload["model_name"],
        routing_metadata.requested_generation_tier,
        routing_metadata.final_generation_tier,
    )
    logger.info("FASHN_DEBUG payload_hash=%s", build_result.payload_hash)
    logger.info(
        "FASHN_DEBUG model_image_hash=%s original=%sx%s final=%sx%s original_bytes=%s final_bytes=%s mime=%s resized=%s cropped=%s compressed=%s converted=%s",
        build_result.model_image.final_hash,
        build_result.model_image.original_width,
        build_result.model_image.original_height,
        build_result.model_image.final_width,
        build_result.model_image.final_height,
        build_result.model_image.original_file_size,
        build_result.model_image.final_file_size,
        build_result.model_image.final_mime_type,
        build_result.model_image.was_resized,
        build_result.model_image.was_cropped,
        build_result.model_image.was_compressed,
        build_result.model_image.was_converted,
    )
    logger.info(
        "FASHN_DEBUG garment_image_hash=%s original=%sx%s final=%sx%s original_bytes=%s final_bytes=%s mime=%s resized=%s cropped=%s compressed=%s converted=%s",
        build_result.garment_image.final_hash,
        build_result.garment_image.original_width,
        build_result.garment_image.original_height,
        build_result.garment_image.final_width,
        build_result.garment_image.final_height,
        build_result.garment_image.original_file_size,
        build_result.garment_image.final_file_size,
        build_result.garment_image.final_mime_type,
        build_result.garment_image.was_resized,
        build_result.garment_image.was_cropped,
        build_result.garment_image.was_compressed,
        build_result.garment_image.was_converted,
    )
    logger.info(
        "FASHN_DEBUG payload=%s",
        json.dumps(
            _redacted_payload(payload, build_result.model_image, build_result.garment_image),
            sort_keys=True,
            ensure_ascii=False,
        ),
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
                _compare_standard_generation_signature(
                    generation_id=generation_id,
                    debug_label=debug_label,
                    routing_metadata=routing_metadata,
                    payload_hash=build_result.payload_hash,
                    model_image=build_result.model_image,
                    garment_image=build_result.garment_image,
                    provider_output_path=output_path,
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
    payload_hash: str,
    routing_metadata: TryOnRoutingMetadata,
    selected_garment_photo_type: str,
    prepared_model_image: PreparedFashnInputImage,
    prepared_garment_image: PreparedFashnInputImage,
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
        _write_json(
            directory / "request_payload.json",
            _redacted_payload(payload, prepared_model_image, prepared_garment_image),
        )
        _write_json(
            directory / "request_metadata.json",
            {
                "model_image": _image_metadata(model_image_path),
                "garment_image": _image_metadata(garment_image_path),
                "prepared_model_image": _prepared_image_metadata(prepared_model_image),
                "prepared_garment_image": _prepared_image_metadata(prepared_garment_image),
                "payload_summary": {
                    "model_name": payload.get("model_name"),
                    "payload_hash": payload_hash,
                    "requested_generation_tier": routing_metadata.requested_generation_tier,
                    "final_generation_tier": routing_metadata.final_generation_tier,
                    "selected_category_code": routing_metadata.category,
                    "normalized_fashn_category": routing_metadata.normalized_fashn_category,
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
        normalized_fashn_category="multiple",
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
) -> FashnPayloadBuildResult:
    try:
        routing_decision = resolve_tryon_routing(
            user_category=category_code,
            requested_generation_tier=requested_generation_tier,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    model_image = _prepare_fashn_input_image(model_image_path)
    garment_image = _prepare_fashn_input_image(garment_image_path)
    normalized_garment_photo_type = (garment_photo_type or settings.FASHN_GARMENT_PHOTO_TYPE or "flat-lay").strip().lower() or "flat-lay"
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

    if routing_decision.final_generation_tier == GenerationTier.premium:
        premium_category = validate_premium_category(routing_decision.premium_category)
        metadata.normalized_fashn_category = premium_category
        prompt_outcome = generate_premium_garment_prompt(garment_image.data_url, routing_decision.user_category)
        metadata.openai_analysis_success = prompt_outcome.openai_analysis_success
        metadata.fallback_used = prompt_outcome.fallback_used
        metadata.generated_prompt = prompt_outcome.prompt
        payload = {
            "model_name": routing_decision.fashn_model_name,
            "inputs": {
                "model_image": model_image.data_url,
                "product_image": garment_image.data_url,
                "prompt": prompt_outcome.prompt,
                "resolution": "1k",
                "generation_mode": "balanced",
                "num_images": 1,
                "output_format": "png",
                "return_base64": True,
            },
        }
        return FashnPayloadBuildResult(
            payload=payload,
            routing_metadata=metadata,
            model_image=model_image,
            garment_image=garment_image,
            payload_hash=_payload_hash(payload),
        )

    standard_category = validate_standard_category(routing_decision.standard_category)
    metadata.normalized_fashn_category = standard_category
    seed = settings.DEBUG_FASHN_SEED if settings.DEBUG and settings.DEBUG_FASHN_SEED is not None else settings.FASHN_SEED
    if seed is None:
        seed = 42

    # Restore the richer v1.6 payload that previously produced more consistent results.
    inputs = {
        "mode": "balanced",
        "category": standard_category,
        "model_image": model_image.data_url,
        "num_samples": 1,
        "garment_image": garment_image.data_url,
        "output_format": "png",
        "return_base64": True,
        "moderation_level": "permissive",
        "segmentation_free": True,
        "garment_photo_type": normalized_garment_photo_type or "flat-lay",
    }
    inputs["seed"] = seed

    payload = {
        "model_name": routing_decision.fashn_model_name,
        "inputs": inputs,
    }
    return FashnPayloadBuildResult(
        payload=payload,
        routing_metadata=metadata,
        model_image=model_image,
        garment_image=garment_image,
        payload_hash=_payload_hash(payload),
    )


def validate_image(image_base64: str) -> bool:
    if not image_base64 or len(image_base64.strip()) < MIN_IMAGE_BASE64_LENGTH:
        return False
    try:
        return _validate_image_bytes(_data_url_bytes(image_base64))
    except Exception:
        return False


def normalize_image(image_base64: str) -> str:
    prepared_image = _prepare_fashn_input_image_from_bytes(
        _data_url_bytes(image_base64),
        fallback_mime_type=_data_url_mime_type(image_base64),
    )
    return prepared_image.data_url


def _normalized_image_to_data_url(path: Path) -> str:
    return _prepare_fashn_input_image(path).data_url


def _prepare_fashn_input_image(path: Path) -> PreparedFashnInputImage:
    fallback_mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return _prepare_fashn_input_image_from_bytes(path.read_bytes(), fallback_mime_type=fallback_mime_type)


def _prepare_fashn_input_image_from_bytes(raw_bytes: bytes, *, fallback_mime_type: str) -> PreparedFashnInputImage:
    if not _validate_image_bytes(raw_bytes):
        raise InvalidTryOnImageError("Invalid image. Please upload a clearer photo.")

    # Keep FASHN inputs as close as possible to the uploaded bytes: no crop, no
    # resize, and only a single conversion when orientation or format requires it.
    with Image.open(BytesIO(raw_bytes)) as original_image:
        original_image.load()
        original_width, original_height = original_image.size
        original_format = (original_image.format or _mime_to_pil_format(fallback_mime_type)).upper()
        original_mime_type = _normalize_mime_type(fallback_mime_type, original_format)
        orientation = _image_orientation(original_image)
        corrected_image = ImageOps.exif_transpose(original_image).copy()

    normalized_for_orientation = orientation not in {None, 1}
    can_passthrough_original = original_mime_type in FASHN_PASSTHROUGH_MIME_TYPES and not normalized_for_orientation

    if can_passthrough_original:
        final_bytes = raw_bytes
        final_mime_type = original_mime_type
        final_width = original_width
        final_height = original_height
        was_compressed = False
        was_converted = False
    else:
        final_format = _preferred_fashn_export_format(corrected_image, original_mime_type)
        final_bytes, final_mime_type = _encode_fashn_image(corrected_image, final_format)
        if not _validate_image_bytes(final_bytes):
            raise InvalidTryOnImageError("Invalid image. Please upload a clearer photo.")
        final_width = corrected_image.width
        final_height = corrected_image.height
        was_compressed = final_mime_type == "image/jpeg"
        was_converted = final_mime_type != original_mime_type or normalized_for_orientation

    return PreparedFashnInputImage(
        data_url=_bytes_to_data_url(final_bytes, final_mime_type),
        final_bytes=final_bytes,
        original_width=original_width,
        original_height=original_height,
        final_width=final_width,
        final_height=final_height,
        original_file_size=len(raw_bytes),
        final_file_size=len(final_bytes),
        original_mime_type=original_mime_type,
        final_mime_type=final_mime_type,
        original_hash=_sha256_hex(raw_bytes),
        final_hash=_sha256_hex(final_bytes),
        was_resized=False,
        was_cropped=False,
        was_compressed=was_compressed,
        was_converted=was_converted,
    )


def _validate_image_bytes(raw_bytes: bytes) -> bool:
    if not raw_bytes or len(raw_bytes) < 1024:
        return False
    try:
        with Image.open(BytesIO(raw_bytes)) as original_image:
            original_image.load()
            image = ImageOps.exif_transpose(original_image)
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


def _data_url_bytes(image_base64: str) -> bytes:
    encoded = image_base64.split(",", 1)[1] if "," in image_base64 else image_base64
    return base64.b64decode(encoded)


def _data_url_mime_type(image_base64: str) -> str:
    if image_base64.startswith("data:") and ";base64," in image_base64:
        return image_base64.split(":", 1)[1].split(";", 1)[0].lower()
    return "image/jpeg"


def _bytes_to_data_url(raw_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(raw_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _normalize_mime_type(fallback_mime_type: str, original_format: str) -> str:
    normalized_fallback = (fallback_mime_type or "").strip().lower()
    if normalized_fallback in {"image/jpeg", "image/png", "image/webp"}:
        return normalized_fallback
    if original_format == "PNG":
        return "image/png"
    if original_format == "WEBP":
        return "image/webp"
    return "image/jpeg"


def _mime_to_pil_format(mime_type: str) -> str:
    normalized = (mime_type or "").strip().lower()
    if normalized == "image/png":
        return "PNG"
    if normalized == "image/webp":
        return "WEBP"
    return "JPEG"


def _preferred_fashn_export_format(image: Image.Image, original_mime_type: str) -> str:
    if _image_has_alpha(image):
        return "PNG"
    if original_mime_type == "image/png":
        return "PNG"
    return "JPEG"


def _encode_fashn_image(image: Image.Image, image_format: str) -> tuple[bytes, str]:
    output = BytesIO()
    if image_format == "PNG":
        image.save(output, format="PNG")
        return output.getvalue(), "image/png"

    image.convert("RGB").save(
        output,
        format="JPEG",
        quality=98,
        subsampling=0,
    )
    return output.getvalue(), "image/jpeg"


def _image_has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)


def _image_orientation(image: Image.Image) -> int | None:
    try:
        return image.getexif().get(274)
    except Exception:
        return None


def _payload_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_hex(serialized.encode("utf-8"))


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _prepared_image_metadata(image: PreparedFashnInputImage) -> dict[str, object]:
    return {
        "original_width": image.original_width,
        "original_height": image.original_height,
        "final_width": image.final_width,
        "final_height": image.final_height,
        "original_file_size": image.original_file_size,
        "final_file_size": image.final_file_size,
        "original_mime_type": image.original_mime_type,
        "final_mime_type": image.final_mime_type,
        "original_hash": image.original_hash,
        "final_hash": image.final_hash,
        "was_resized": image.was_resized,
        "was_cropped": image.was_cropped,
        "was_compressed": image.was_compressed,
        "was_converted": image.was_converted,
    }


def _redacted_payload(
    payload: dict,
    model_image: PreparedFashnInputImage,
    garment_image: PreparedFashnInputImage,
) -> dict[str, object]:
    sanitized = json.loads(json.dumps(payload))
    inputs = sanitized.get("inputs", {})
    if "model_image" in inputs:
        inputs["model_image"] = {
            "redacted": True,
            "mime_type": model_image.final_mime_type,
            "bytes": model_image.final_file_size,
            "sha256": model_image.final_hash,
            "dimensions": [model_image.final_width, model_image.final_height],
        }
    garment_field = "product_image" if "product_image" in inputs else "garment_image"
    if garment_field in inputs:
        inputs[garment_field] = {
            "redacted": True,
            "mime_type": garment_image.final_mime_type,
            "bytes": garment_image.final_file_size,
            "sha256": garment_image.final_hash,
            "dimensions": [garment_image.final_width, garment_image.final_height],
        }
    return sanitized


def _compare_standard_generation_signature(
    *,
    generation_id: str,
    debug_label: str,
    routing_metadata: TryOnRoutingMetadata,
    payload_hash: str,
    model_image: PreparedFashnInputImage,
    garment_image: PreparedFashnInputImage,
    provider_output_path: Path,
) -> None:
    if not settings.DEBUG or routing_metadata.final_generation_tier != GenerationTier.standard.value:
        return

    signature_dir = settings.fashn_debug_dir / "standard_signatures"
    signature_dir.mkdir(parents=True, exist_ok=True)

    source_signature = _sha256_hex(
        "|".join(
            [
                routing_metadata.category,
                model_image.original_hash,
                garment_image.original_hash,
                routing_metadata.final_generation_tier,
            ]
        ).encode("utf-8")
    )
    output_hash = _sha256_hex(provider_output_path.read_bytes())
    current_record = {
        "generation_id": generation_id,
        "debug_label": debug_label,
        "category": routing_metadata.category,
        "payload_hash": payload_hash,
        "model_image_hash": model_image.final_hash,
        "garment_image_hash": garment_image.final_hash,
        "output_hash": output_hash,
        "created_at": int(time.time()),
    }
    signature_path = signature_dir / f"{source_signature}.json"
    previous_record = None
    if signature_path.exists():
        try:
            previous_record = json.loads(signature_path.read_text(encoding="utf-8"))
        except Exception:
            previous_record = None

    if previous_record:
        preprocessing_changed = any(
            previous_record.get(key) != current_record[key]
            for key in ("payload_hash", "model_image_hash", "garment_image_hash")
        )
        if preprocessing_changed:
            logger.warning(
                "FASHN_DEBUG identical_source_inputs_different_hashes source_signature=%s previous_payload_hash=%s current_payload_hash=%s previous_model_image_hash=%s current_model_image_hash=%s previous_garment_image_hash=%s current_garment_image_hash=%s",
                source_signature,
                previous_record.get("payload_hash"),
                current_record["payload_hash"],
                previous_record.get("model_image_hash"),
                current_record["model_image_hash"],
                previous_record.get("garment_image_hash"),
                current_record["garment_image_hash"],
            )
        elif previous_record.get("output_hash") != current_record["output_hash"]:
            logger.info(
                "FASHN_DEBUG identical_input_hashes_diff_output source_signature=%s previous_output_hash=%s current_output_hash=%s conclusion=tryon-v1.6_non_deterministic",
                source_signature,
                previous_record.get("output_hash"),
                current_record["output_hash"],
            )
        else:
            logger.info(
                "FASHN_DEBUG identical_input_hashes_identical_output source_signature=%s output_hash=%s",
                source_signature,
                current_record["output_hash"],
            )

    signature_path.write_text(json.dumps(current_record, indent=2, ensure_ascii=False), encoding="utf-8")


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
