from __future__ import annotations

import base64
import mimetypes
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.garment_asset import GarmentAsset
from app.models.tryon_job import TryOnJob
from app.services.catvton_runtime import get_catvton_runtime


def run_tryon_job(db: Session, job: TryOnJob) -> TryOnJob:
    upper_asset = _get_asset(db, job.upper_garment_asset_id)
    lower_asset = _get_asset(db, job.lower_garment_asset_id)

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

    job.result_image_url = str(result_path)
    job.status = "completed"
    job.error_message = None
    db.commit()
    db.refresh(job)
    return job


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
            "mode": "balanced",
            "seed": 42,
            "num_samples": 1,
            "output_format": settings.FASHN_OUTPUT_FORMAT,
            "return_base64": settings.FASHN_RETURN_BASE64,
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
