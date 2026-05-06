from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

VISION_MODEL_NAME = "gpt-4o-mini"
TRYON_MODEL_NAME = "tryon-v1.6"
SYSTEM_MESSAGE = "You describe clothing items precisely for virtual try-on systems."
USER_PROMPT = "Describe this clothing item as: color + fit + type (max 6 words). No sentence."
FALLBACK_PROMPT = "clothing item"

_client: OpenAI | None = None


def _get_openai_client() -> OpenAI:
    global _client
    if not settings.OPENAI_API_KEY.strip():
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def generate_prompt_from_image(image_url: str) -> str:
    messages = [
        {
            "role": "system",
            "content": SYSTEM_MESSAGE,
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": USER_PROMPT,
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                },
            ],
        },
    ]

    response = _get_openai_client().chat.completions.create(
        model=VISION_MODEL_NAME,
        messages=messages,
        max_tokens=30,
        temperature=0,
    )
    prompt = _sanitize_prompt(response.choices[0].message.content or "")
    if not prompt:
        raise RuntimeError("OpenAI returned an empty clothing prompt.")

    print(f"GENERATED PROMPT: {prompt}")
    logger.info("GENERATED PROMPT: %s", prompt)
    return prompt


def run_tryon(user_image_url: str, garment_image_url: str) -> dict[str, Any]:
    payload = {
        "model": TRYON_MODEL_NAME,
        "model_image": user_image_url,
        "product_image": garment_image_url,
    }

    print("USING MODEL:", payload["model"])
    print("USER IMAGE:", user_image_url)
    print("GARMENT IMAGE:", garment_image_url)
    print("FINAL PAYLOAD:", payload)
    logger.info("FINAL PAYLOAD: %s", payload)

    with httpx.Client(timeout=settings.TRYON_TIMEOUT_SECONDS) as client:
        response = client.post(settings.EXTERNAL_TRYON_API_URL, json=payload)
        print("FASHN RESPONSE:", response.text)
        logger.info("TRYON RESPONSE STATUS: %s", response.status_code)
        logger.info("TRYON RESPONSE BODY: %s", response.text)
        response.raise_for_status()
        return response.json()


def _sanitize_prompt(value: str) -> str:
    cleaned = value.strip().strip('"').strip("'")
    cleaned = re.sub(r"[^\w\s-]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    return " ".join(cleaned.split()[:6])
