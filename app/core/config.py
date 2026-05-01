from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "development"
    DEBUG: bool = True
    PROJECT_NAME: str = "TryClothes Backend"
    API_V1_PREFIX: str = "/api/v1"

    SECRET_KEY: str = "change-this-secret-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    ALLOWED_CORS_ORIGINS: List[str] = ["*"]
    APP_PUBLIC_BASE_URL: str = "http://127.0.0.1:8000"

    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/vton_fit_soft"
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    EMAIL_DELIVERY_MODE: str = "console"
    EMAIL_FROM: str = "no-reply@tryclothes.app"
    EMAIL_FROM_NAME: str = "TryClothes"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    RESEND_API_KEY: str = ""
    RESEND_API_BASE_URL: str = "https://api.resend.com"
    EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 60

    AUTO_CREATE_TABLES: bool = True
    AUTO_SEED_REFERENCE_DATA: bool = True
    TRYON_PROVIDER: str = "stub"
    TRYON_TIMEOUT_SECONDS: int = 180
    TRYON_POLL_INTERVAL_SECONDS: int = 2
    TRYON_NUM_INFERENCE_STEPS: int = 22
    TRYON_GUIDANCE_SCALE: float = 2.5
    TRYON_SEED: int = 42
    TRYON_RESULT_FORMAT: str = "jpeg"
    TRYON_RESULT_JPEG_QUALITY: int = 92

    FASHN_API_KEY: str = ""
    FASHN_BASE_URL: str = "https://api.fashn.ai/v1"
    FASHN_MODEL_NAME: str = "tryon-v1.6"
    FASHN_GARMENT_PHOTO_TYPE: str = "flat-lay"
    FASHN_OUTPUT_FORMAT: str = "jpeg"
    FASHN_SEGMENTATION_FREE: bool = True
    FASHN_MODERATION_LEVEL: str = "permissive"
    FASHN_RETURN_BASE64: bool = False

    MODEL_COMMAND_TEMPLATE: str = ""
    MODEL_COMMAND_WORKDIR: str = ""

    CATVTON_PROJECT_DIR: str = ""
    CATVTON_BASE_MODEL_PATH: str = "booksforcharlie/stable-diffusion-inpainting"
    CATVTON_RESUME_PATH: str = "zhengchong/CatVTON"
    CATVTON_DEVICE: str = "cuda"
    CATVTON_MIXED_PRECISION: str = "bf16"
    CATVTON_ALLOW_TF32: bool = True
    CATVTON_WIDTH: int = 768
    CATVTON_HEIGHT: int = 1024
    CATVTON_BLUR_FACTOR: int = 9
    CATVTON_REPAINT: bool = True
    CATVTON_PRELOAD_ON_STARTUP: bool = True
    CATVTON_MASK_CACHE_SIZE: int = 16
    CATVTON_FAIL_FAST: bool = False
    CATVTON_PRESERVE_ORIGINAL_RESOLUTION: bool = False

    MAX_UPLOAD_SIZE_MB: int = 15
    MAX_UPLOAD_SIZE_BYTES: int = 15 * 1024 * 1024
    ALLOWED_IMAGE_MIME_TYPES: List[str] = ["image/jpeg", "image/png", "image/webp"]

    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def storage_dir(self) -> Path:
        return self.base_dir / "storage"

    @property
    def person_upload_dir(self) -> Path:
        return self.storage_dir / "persons"

    @property
    def garment_upload_dir(self) -> Path:
        return self.storage_dir / "garments"

    @property
    def tryon_result_dir(self) -> Path:
        return self.storage_dir / "results"

    @field_validator("TRYON_PROVIDER")
    @classmethod
    def normalize_tryon_provider(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def email_delivery_mode_normalized(self) -> str:
        return self.EMAIL_DELIVERY_MODE.strip().lower()

    @property
    def email_ready(self) -> bool:
        mode = self.email_delivery_mode_normalized
        if mode == "console":
            return True
        if mode == "resend":
            return all(
                [
                    self.RESEND_API_KEY.strip(),
                    self.EMAIL_FROM.strip(),
                ]
            )
        if mode == "smtp":
            return all(
                [
                    self.SMTP_HOST.strip(),
                    self.SMTP_USERNAME.strip(),
                    self.SMTP_PASSWORD.strip(),
                    self.EMAIL_FROM.strip(),
                ]
            )
        return False

    @property
    def email_can_deliver_to_real_inboxes(self) -> bool:
        return self.email_delivery_mode_normalized in {"smtp", "resend"} and self.email_ready

    @property
    def runtime_workdir(self) -> Path:
        if self.MODEL_COMMAND_WORKDIR:
            return Path(self.MODEL_COMMAND_WORKDIR).expanduser()
        return self.base_dir

    @property
    def catvton_project_dir(self) -> Path:
        if self.CATVTON_PROJECT_DIR:
            return Path(self.CATVTON_PROJECT_DIR).expanduser()
        return self.base_dir.parent / "CatVTON"


settings = Settings()

for folder in (
    settings.storage_dir,
    settings.person_upload_dir,
    settings.garment_upload_dir,
    settings.tryon_result_dir,
):
    folder.mkdir(parents=True, exist_ok=True)
