from __future__ import annotations

import logging
import os
from functools import lru_cache

from openai import AuthenticationError, OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_openai_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        api_key = settings.OPENAI_API_KEY.strip()
    if api_key.lower().startswith("bearer "):
        api_key = api_key.split(None, 1)[1].strip()
        logger.warning("OPENAI_DEBUG removed unexpected Bearer prefix from OPENAI_API_KEY")
    logger.info("OPENAI_DEBUG key_present=%s key_length=%s", bool(api_key), len(api_key))
    if api_key and not api_key.startswith("sk-"):
        logger.warning("OPENAI_DEBUG OPENAI_API_KEY has unexpected format")
    return api_key


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    api_key = get_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=api_key)


def run_openai_startup_self_test() -> None:
    api_key = get_openai_api_key()
    if not api_key:
        logger.info("OpenAI startup self-test skipped: OPENAI_API_KEY missing")
        return

    try:
        client = get_openai_client()
        client.models.list()
        logger.info("OpenAI startup self-test succeeded")
    except AuthenticationError:
        logger.error("OpenAI authentication failed")
    except Exception as exc:  # pragma: no cover - external provider diagnostics
        logger.warning("OpenAI startup self-test skipped due to runtime error: %s", exc)
