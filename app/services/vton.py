from __future__ import annotations

import base64
import mimetypes
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.garment_asset import GarmentAsset
from app.models.tryon_job import TryOnJob
from app.services.catvton_runtime import get_catvton_runtime


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
    processed_job, _ = run_tryon_job_with_metrics(db, job)
    return processed_job


def run_tryon_job_with_metrics(db: Session, job: TryOnJob) -> tuple[TryOnJob, OperationPerformance]:
    upper_asset = _get_asset(db, job.upper_garment_asset_id)
    lower_asset = _get_asset(db, job.lower_garment_asset_id)
    operation_started = time.monotonic()
    performance = OperationPerformance()

    try:
        if settings.TRYON_PROVIDER == "stub":
            result_path = _run_stub(job, upper_asset, lower_asset, db)
        elif settings.TRYON_PROVIDER == "fashn_api":
            result_path = _run_fashn_two_pass(job, upper_asset, lower_asset, db)
        elif settings.TRYON_PROVIDER == "catvton":
            result_path = _run_catvton_two_pass(job, upper_asset, lower_asset, db)
        elif settings.TRYON_PROVIDER == "command":
            result_path = _run_command_two_pass(job, upper_asset, lower_asset, db)
        else:
            raise RuntimeError(f"Unsupported TRYON_PROVIDER: {settings.TRYON_PROVIDER}")
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:2000]
        db.commit()
        db.refresh(job)
        raise

    performance.processing_ms = int((time.monotonic() - operation_started) * 1000)
    finalize_started = time.monotonic()
    job.result_image_url = str(result_path)
    job.status = "completed"
    job.error_message = None
    db.commit()
    db.refresh(job)
    performance.finalize_ms = int((time.monotonic() - finalize_started) * 1000)
    performance.total_ms = int((time.monotonic() - operation_started) * 1000)
    return job, performance


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
) -> Path:
    if not settings.FASHN_API_KEY:
        raise RuntimeError("FASHN_API_KEY is not configured.")

    current_image_path = Path(job.person_image_url)
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
        current_image_path = _run_fashn_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(upper_asset.image_url),
            category="tops",
            output_path=settings.tryon_result_dir / f"{job.id}_upper_pass.{settings.FASHN_OUTPUT_FORMAT}",
        )

    if lower_asset:
        job.status = "processing_lower"
        db.commit()
        current_image_path = _run_fashn_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(lower_asset.image_url),
            category="bottoms",
            output_path=settings.tryon_result_dir / f"{job.id}_lower_pass.{settings.FASHN_OUTPUT_FORMAT}",
        )

    final_path = settings.tryon_result_dir / _result_filename(job.id)
    return _finalize_result_file(current_image_path, final_path)


def _run_command_two_pass(
    job: TryOnJob,
    upper_asset: Optional[GarmentAsset],
    lower_asset: Optional[GarmentAsset],
    db: Session,
) -> Path:
    current_image_path = Path(job.person_image_url)
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
        current_image_path = _run_command_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(upper_asset.image_url),
            category=_command_category(upper_asset, "tops"),
            output_path=settings.tryon_result_dir / f"{job.id}_upper_pass.png",
        )

    if lower_asset:
        job.status = "processing_lower"
        db.commit()
        current_image_path = _run_command_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(lower_asset.image_url),
            category=_command_category(lower_asset, "bottoms"),
            output_path=settings.tryon_result_dir / f"{job.id}_lower_pass.png",
        )

    final_path = settings.tryon_result_dir / _result_filename(job.id)
    return _finalize_result_file(current_image_path, final_path)


def _run_catvton_two_pass(
    job: TryOnJob,
    upper_asset: Optional[GarmentAsset],
    lower_asset: Optional[GarmentAsset],
    db: Session,
) -> Path:
    current_image_path = Path(job.person_image_url)
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
        current_image_path = _run_catvton_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(upper_asset.image_url),
            category=_command_category(upper_asset, "upper"),
            output_path=settings.tryon_result_dir / f"{job.id}_upper_pass.png",
        )

    if lower_asset:
        job.status = "processing_lower"
        db.commit()
        current_image_path = _run_catvton_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(lower_asset.image_url),
            category=_command_category(lower_asset, "lower"),
            output_path=settings.tryon_result_dir / f"{job.id}_lower_pass.png",
        )

    final_path = settings.tryon_result_dir / _result_filename(job.id)
    return _finalize_result_file(current_image_path, final_path)


def _run_fashn_pass(model_image_path: Path, garment_image_path: Path, category: str, output_path: Path) -> Path:
    headers = {
        "Authorization": f"Bearer {settings.FASHN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model_name": settings.FASHN_MODEL_NAME,
        "inputs": {
            "model_image": _image_to_data_url(model_image_path),
            "garment_image": _image_to_data_url(garment_image_path),
            "category": category,
            "garment_photo_type": settings.FASHN_GARMENT_PHOTO_TYPE,
            "segmentation_free": settings.FASHN_SEGMENTATION_FREE,
            "moderation_level": settings.FASHN_MODERATION_LEVEL,
            "mode": "performance",
            "seed": 42,
            "num_samples": 1,
            "output_format": "jpeg",
            "return_base64": False,
        },
    }

    with httpx.Client(timeout=settings.TRYON_TIMEOUT_SECONDS) as client:
        response = client.post(f"{settings.FASHN_BASE_URL}/run", headers=headers, json=payload)
        response.raise_for_status()
        prediction_id = response.json().get("id")
        if not prediction_id:
            raise RuntimeError(f"FASHN did not return a prediction id: {response.text[:1000]}")

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
                _persist_output(outputs[0], output_path)
                return output_path
            if status_value == "failed":
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
    if result_format == "png":
        if source_path != final_path:
            final_path.write_bytes(source_path.read_bytes())
        return final_path

    with Image.open(source_path) as source_image:
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


def _get_asset(db: Session, asset_id: Optional[str]) -> Optional[GarmentAsset]:
    if not asset_id:
        return None
    return db.query(GarmentAsset).filter(GarmentAsset.id == asset_id).first()


def _command_category(asset: GarmentAsset, fallback: str) -> str:
    if asset.category and asset.category.code:
        return asset.category.code
    return fallback
