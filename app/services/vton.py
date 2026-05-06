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
from pathlib import Path
from typing import Callable, Optional

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.garment_asset import GarmentAsset
from app.models.tryon_job import TryOnJob
from app.services.catvton_runtime import get_catvton_runtime

logger = logging.getLogger(__name__)


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


def run_tryon_job_with_metrics(
    db: Session,
    job: TryOnJob,
    *,
    upper_category_code: Optional[str] = None,
    lower_category_code: Optional[str] = None,
    upper_garment_photo_type: Optional[str] = None,
    lower_garment_photo_type: Optional[str] = None,
) -> tuple[TryOnJob, OperationPerformance]:
    upper_asset = _get_asset(db, job.upper_garment_asset_id)
    lower_asset = _get_asset(db, job.lower_garment_asset_id)
    operation_started = time.monotonic()
    performance = OperationPerformance()

    try:
        if settings.TRYON_PROVIDER == "stub":
            result_path = _run_stub(job, upper_asset, lower_asset, db)
        elif settings.TRYON_PROVIDER == "fashn_api":
            result_path = _run_fashn_two_pass(
                job,
                upper_asset,
                lower_asset,
                db,
                upper_category_code=upper_category_code,
                lower_category_code=lower_category_code,
                upper_garment_photo_type=upper_garment_photo_type,
                lower_garment_photo_type=lower_garment_photo_type,
            )
        elif settings.TRYON_PROVIDER == "catvton":
            result_path = _run_catvton_two_pass(
                job,
                upper_asset,
                lower_asset,
                db,
                upper_category_code=upper_category_code,
                lower_category_code=lower_category_code,
            )
        elif settings.TRYON_PROVIDER == "command":
            result_path = _run_command_two_pass(
                job,
                upper_asset,
                lower_asset,
                db,
                upper_category_code=upper_category_code,
                lower_category_code=lower_category_code,
            )
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
    *,
    upper_category_code: Optional[str] = None,
    lower_category_code: Optional[str] = None,
    upper_garment_photo_type: Optional[str] = None,
    lower_garment_photo_type: Optional[str] = None,
) -> Path:
    if not settings.FASHN_API_KEY:
        raise RuntimeError("FASHN_API_KEY is not configured.")

    current_image_path = Path(job.person_image_url)
    if upper_asset:
        job.status = "processing_upper"
        db.commit()
        selected_upper_category_code = _selected_category_code(upper_category_code, upper_asset, "tshirt")
        current_image_path = _run_fashn_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(upper_asset.image_url),
            category_code=selected_upper_category_code,
            output_path=settings.tryon_result_dir / f"{job.id}_upper_pass.{settings.FASHN_OUTPUT_FORMAT}",
            garment_photo_type=_resolved_garment_photo_type(upper_garment_photo_type, upper_asset),
            debug_label=f"{job.id}_upper_pass",
        )

    if lower_asset:
        job.status = "processing_lower"
        db.commit()
        selected_lower_category_code = _selected_category_code(lower_category_code, lower_asset, "pants")
        current_image_path = _run_fashn_pass(
            model_image_path=current_image_path,
            garment_image_path=Path(lower_asset.image_url),
            category_code=selected_lower_category_code,
            output_path=settings.tryon_result_dir / f"{job.id}_lower_pass.{settings.FASHN_OUTPUT_FORMAT}",
            garment_photo_type=_resolved_garment_photo_type(lower_garment_photo_type, lower_asset),
            debug_label=f"{job.id}_lower_pass",
        )

    final_path = settings.tryon_result_dir / _result_filename(job.id)
    return _finalize_result_file(current_image_path, final_path)


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
) -> Path:
    headers = {
        "Authorization": f"Bearer {settings.FASHN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = _build_fashn_payload(
        model_image_path=model_image_path,
        garment_image_path=garment_image_path,
        category_code=category_code,
        garment_photo_type=garment_photo_type,
    )
    print("USING MODEL:", payload["model_name"])
    print("USER IMAGE:", str(model_image_path))
    print("GARMENT IMAGE:", str(garment_image_path))
    debug_directory = _persist_fashn_debug_request(
        debug_label=debug_label,
        model_image_path=model_image_path,
        garment_image_path=garment_image_path,
        payload=payload,
        selected_category_code=category_code,
        selected_garment_photo_type=garment_photo_type,
    )
    model_metadata = _image_metadata(model_image_path)
    garment_metadata = _image_metadata(garment_image_path)
    logger.info(
        "fashn-request label=%s model_name=%s selected_category_code=%s request_category=%s garment_photo_type=%s mode=%s output_format=%s segmentation_free=%s return_base64=%s model=%sx%s/%sB garment=%sx%s/%sB",
        debug_label,
        payload["model_name"],
        category_code,
        payload.get("inputs", {}).get("category"),
        garment_photo_type,
        settings.FASHN_MODE,
        settings.FASHN_OUTPUT_FORMAT,
        settings.FASHN_SEGMENTATION_FREE,
        settings.FASHN_RETURN_BASE64,
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
        if debug_directory is not None:
            _write_json(debug_directory / "submit_response.json", response.json())
            (debug_directory / "prediction_id.txt").write_text(prediction_id, encoding="utf-8")

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
                return output_path
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
    selected_category_code: str,
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
                    "selected_category_code": selected_category_code,
                    "selected_garment_photo_type": selected_garment_photo_type,
                    "category": payload.get("inputs", {}).get("category"),
                    "garment_photo_type": payload.get("inputs", {}).get("garment_photo_type"),
                    "mode": payload.get("inputs", {}).get("mode"),
                    "output_format": payload.get("inputs", {}).get("output_format"),
                    "segmentation_free": payload.get("inputs", {}).get("segmentation_free"),
                    "moderation_level": payload.get("inputs", {}).get("moderation_level"),
                    "return_base64": payload.get("inputs", {}).get("return_base64"),
                    "seed": payload.get("inputs", {}).get("seed"),
                    "num_samples": payload.get("inputs", {}).get("num_samples"),
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


def _build_fashn_payload(
    *,
    model_image_path: Path,
    garment_image_path: Path,
    category_code: str,
    garment_photo_type: str,
) -> dict:
    request_category = _fashn_request_category(category_code)
    model_name = "tryon-v1.6"
    payload = {
        "model_name": model_name,
        "inputs": {
            "model_image": _image_to_data_url(model_image_path),
            "garment_image": _image_to_data_url(garment_image_path),
            "category": request_category,
            "garment_photo_type": garment_photo_type,
            "segmentation_free": settings.FASHN_SEGMENTATION_FREE,
            "moderation_level": settings.FASHN_MODERATION_LEVEL,
            "mode": settings.FASHN_MODE,
            "output_format": settings.FASHN_OUTPUT_FORMAT,
            "return_base64": settings.FASHN_RETURN_BASE64,
        },
    }
    if settings.FASHN_SEED is not None:
        payload["inputs"]["seed"] = settings.FASHN_SEED
    if settings.FASHN_NUM_SAMPLES > 0:
        payload["inputs"]["num_samples"] = settings.FASHN_NUM_SAMPLES
    return payload


def _fashn_request_category(category_code: str) -> str:
    normalized = category_code.strip().lower()
    mapping = {
        "top": "tops",
        "tops": "tops",
        "tshirt": "tops",
        "tee": "tops",
        "shirt": "tops",
        "hoodie": "tops",
        "blouse": "tops",
        "sweater": "tops",
        "jacket": "tops",
        "coat": "tops",
        "bottom": "bottoms",
        "bottoms": "bottoms",
        "pants": "bottoms",
        "trousers": "bottoms",
        "jeans": "bottoms",
        "skirt": "bottoms",
        "shorts": "bottoms",
        "dress": "one-pieces",
        "jumpsuit": "one-pieces",
        "one-piece": "one-pieces",
        "one_pieces": "one-pieces",
        "one-pieces": "one-pieces",
    }
    return mapping.get(normalized, normalized)
