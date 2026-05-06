from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import settings
from app.services.tryon_routing import normalize_category

logger = logging.getLogger(__name__)

VISION_MODEL_NAME = "gpt-4o-mini"
SYSTEM_MESSAGE = "You analyze garments for premium virtual try-on reconstruction."
USER_PROMPT = (
    "Describe this garment for virtual try-on in one concise factual sentence. "
    "Include color, garment type, fit, fabric or texture, sleeves, pattern, silhouette, length, layering, "
    "and any important visual traits. No marketing language."
)

_client: OpenAI | None = None


@dataclass(frozen=True)
class GarmentPromptOutcome:
    prompt: str
    openai_analysis_success: bool
    fallback_used: bool


def generate_premium_garment_prompt(image_reference: str, user_category: str) -> GarmentPromptOutcome:
    fallback_prompt = fallback_prompt_for_category(user_category)
    if not settings.OPENAI_API_KEY.strip():
        logger.warning("OPENAI_API_KEY is not configured. Using premium fallback prompt.")
        return GarmentPromptOutcome(prompt=fallback_prompt, openai_analysis_success=False, fallback_used=True)

    try:
        response = _get_openai_client().chat.completions.create(
            model=VISION_MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_MESSAGE,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": USER_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_reference}},
                    ],
                },
            ],
            max_tokens=80,
            temperature=0,
        )
        prompt = _sanitize_prompt(response.choices[0].message.content or "")
        if not prompt:
            raise RuntimeError("OpenAI returned an empty premium garment prompt.")

        logger.info("premium-prompt success category=%s prompt=%s", user_category, prompt)
        return GarmentPromptOutcome(prompt=prompt, openai_analysis_success=True, fallback_used=False)
    except Exception as exc:  # pragma: no cover - external provider fallback
        logger.warning("premium-prompt fallback category=%s error=%s", user_category, exc)
        return GarmentPromptOutcome(prompt=fallback_prompt, openai_analysis_success=False, fallback_used=True)


def fallback_prompt_for_category(user_category: str) -> str:
    normalized = normalize_category(user_category)
    mapping = {
        "hoodie": "oversized hoodie with realistic fabric folds",
        "hanorac": "oversized hoodie with realistic fabric folds",
        "dress": "elegant dress with realistic fabric folds",
        "rochie": "elegant dress with realistic fabric folds",
        "jeans": "denim jeans with realistic seams and fabric texture",
        "blugi": "denim jeans with realistic seams and fabric texture",
        "tshirt": "structured t-shirt with realistic cotton texture",
        "tricou": "structured t-shirt with realistic cotton texture",
        "shirt": "structured shirt with realistic fabric drape",
        "camasa": "structured shirt with realistic fabric drape",
        "cămașă": "structured shirt with realistic fabric drape",
        "jacket": "tailored jacket with realistic structure and fabric depth",
        "geaca": "tailored jacket with realistic structure and fabric depth",
        "geacă": "tailored jacket with realistic structure and fabric depth",
    }
    return mapping.get(normalized, "realistic clothing item with accurate fit and fabric texture")


def _get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _sanitize_prompt(value: str) -> str:
    cleaned = value.strip().strip('"').strip("'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    if len(words) > 26:
        cleaned = " ".join(words[:26])
    return cleaned
