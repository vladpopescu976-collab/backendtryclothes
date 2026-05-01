from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Optional
from uuid import uuid4

from app.core.config import settings
from app.services.vton import generate_video_from_result_image_with_metrics

logger = logging.getLogger(__name__)


@dataclass
class VideoJobRecord:
    id: str
    owner_key: str
    image_path: str
    prompt: Optional[str]
    duration_seconds: int
    resolution: str
    provider: str
    status: str = "queued"
    status_message: Optional[str] = "Uploading image"
    video_url: Optional[str] = None
    error_message: Optional[str] = None
    performance: Optional[dict[str, int | None]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class VideoJobStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._jobs: dict[str, VideoJobRecord] = {}

    def create_job(
        self,
        *,
        owner_key: str,
        image_path: Path,
        prompt: Optional[str],
        duration_seconds: int,
        resolution: str,
    ) -> VideoJobRecord:
        record = VideoJobRecord(
            id=uuid4().hex,
            owner_key=owner_key,
            image_path=str(image_path),
            prompt=prompt,
            duration_seconds=duration_seconds,
            resolution=resolution,
            provider=settings.TRYON_PROVIDER,
        )
        with self._lock:
            self._jobs[record.id] = record
        return record

    def get_job(self, job_id: str, owner_key: str) -> Optional[VideoJobRecord]:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.owner_key != owner_key:
                return None
            return self._copy(record)

    def update_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        status_message: Optional[str] = None,
        video_url: Optional[str] = None,
        error_message: Optional[str] = None,
        performance: Optional[dict[str, int | None]] = None,
    ) -> Optional[VideoJobRecord]:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                return None

            if status is not None:
                record.status = status
            if status_message is not None:
                record.status_message = status_message
            if video_url is not None:
                record.video_url = video_url
            if error_message is not None:
                record.error_message = error_message
            if performance is not None:
                record.performance = performance
            record.updated_at = datetime.now(timezone.utc)
            return self._copy(record)

    @staticmethod
    def _copy(record: VideoJobRecord) -> VideoJobRecord:
        return VideoJobRecord(
            id=record.id,
            owner_key=record.owner_key,
            image_path=record.image_path,
            prompt=record.prompt,
            duration_seconds=record.duration_seconds,
            resolution=record.resolution,
            provider=record.provider,
            status=record.status,
            status_message=record.status_message,
            video_url=record.video_url,
            error_message=record.error_message,
            performance=dict(record.performance) if record.performance else None,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


video_job_store = VideoJobStore()


def owner_key_for_user(user_id: str) -> str:
    return f"user:{user_id}"


def schedule_video_job(record: VideoJobRecord) -> None:
    asyncio.create_task(_run_video_job(record.id))


async def _run_video_job(job_id: str) -> None:
    record = video_job_store.update_job(
        job_id,
        status="processing",
        status_message="Generating video",
    )
    if not record:
        return

    def status_callback(status: str, detail: Optional[str]) -> None:
        mapped_status = "finalizing" if status == "finalizing" else "processing"
        mapped_detail = detail or ("Finalizing" if mapped_status == "finalizing" else "Generating video")
        video_job_store.update_job(job_id, status=mapped_status, status_message=mapped_detail)

    try:
        outcome = await asyncio.to_thread(
            generate_video_from_result_image_with_metrics,
            image_path=Path(record.image_path),
            prompt=record.prompt,
            duration_seconds=record.duration_seconds,
            resolution=record.resolution,
            status_callback=status_callback,
        )
    except Exception as exc:
        logger.exception("Video generation job %s failed", job_id)
        video_job_store.update_job(
            job_id,
            status="failed",
            status_message="Failed",
            error_message=str(exc)[:2000],
        )
        return

    video_job_store.update_job(
        job_id,
        status="ready",
        status_message="Ready",
        video_url=outcome.video_url,
        performance=outcome.performance.as_dict(),
    )
