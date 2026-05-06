from __future__ import annotations

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.base import Base
from app.db.models import *  # noqa: F401,F403
from app.db.session import SessionLocal, engine
from app.services.catvton_runtime import get_catvton_runtime_status, preload_catvton_runtime
from app.services.openai_client import run_openai_startup_self_test
from app.services.seed import seed_reference_data

app = FastAPI(title=settings.PROJECT_NAME, debug=settings.DEBUG, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event() -> None:
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)

    if settings.AUTO_SEED_REFERENCE_DATA:
        db = SessionLocal()
        try:
            seed_reference_data(db)
        finally:
            db.close()

    if settings.TRYON_PROVIDER == "catvton" and settings.CATVTON_PRELOAD_ON_STARTUP:
        try:
            preload_catvton_runtime()
        except Exception:
            if settings.CATVTON_FAIL_FAST:
                raise

    run_openai_startup_self_test()


@app.get("/")
def root() -> dict:
    return {
        "message": f"{settings.PROJECT_NAME} is running.",
        "docs_url": "/docs",
        "api_prefix": settings.API_V1_PREFIX,
        "tryon_provider": settings.TRYON_PROVIDER,
    }


@app.get("/ping")
def ping() -> Response:
    if settings.TRYON_PROVIDER == "fashn_api" and not settings.FASHN_API_KEY.strip():
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    if settings.TRYON_PROVIDER != "catvton":
        return Response(status_code=status.HTTP_200_OK)

    runtime_status = get_catvton_runtime_status()
    if runtime_status.get("load_error"):
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(status_code=status.HTTP_200_OK)


app.include_router(api_router, prefix=settings.API_V1_PREFIX)
