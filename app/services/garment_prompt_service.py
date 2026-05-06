from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from app.services.openai_client import get_openai_api_key, get_openai_client
from app.services.tryon_routing import normalize_category

logger = logging.getLogger(__name__)

VISION_MODEL_NAME = "gpt-4o-mini"
SYSTEM_MESSAGE = (
    "You describe garments for AI try-on reconstruction using compact comma-separated visual descriptors."
)
USER_PROMPT = (
    "Describe this garment for AI try-on reconstruction using short comma-separated visual descriptors. "
    "Mention garment type, fit, material, texture and important details. Maximum 18 words."
)

@dataclass(frozen=True)
class GarmentPromptOutcome:
    prompt: str
    openai_analysis_success: bool
    fallback_used: bool


def generate_premium_garment_prompt(image_reference: str, user_category: str) -> GarmentPromptOutcome:
    fallback_prompt = fallback_prompt_for_category(user_category)
    if not get_openai_api_key():
        logger.warning("OPENAI_API_KEY is not configured. Using premium fallback prompt.")
        logger.info(
            'PREMIUM_PROMPT_DEBUG generated_prompt="%s" response_ms=%s total_tokens=%s output_words=%s output_chars=%s fallback_used=%s',
            fallback_prompt,
            0,
            0,
            len(fallback_prompt.split()),
            len(fallback_prompt),
            True,
        )
        return GarmentPromptOutcome(prompt=fallback_prompt, openai_analysis_success=False, fallback_used=True)

    try:
        started_at = time.monotonic()
        response = get_openai_client().chat.completions.create(
            model=VISION_MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_MESSAGE,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"{USER_PROMPT} Category hint: {normalize_category(user_category) or 'unknown'}.",
                        },
                        {"type": "image_url", "image_url": {"url": image_reference}},
                    ],
                },
            ],
            max_tokens=40,
            temperature=0,
        )
        prompt = _sanitize_prompt(response.choices[0].message.content or "")
        if not prompt:
            raise RuntimeError("OpenAI returned an empty premium garment prompt.")

        response_ms = int((time.monotonic() - started_at) * 1000)
        total_tokens = getattr(response.usage, "total_tokens", 0) if getattr(response, "usage", None) else 0
        logger.info("premium-prompt success category=%s prompt=%s", user_category, prompt)
        logger.info(
            'PREMIUM_PROMPT_DEBUG generated_prompt="%s" response_ms=%s total_tokens=%s output_words=%s output_chars=%s fallback_used=%s',
            prompt,
            response_ms,
            total_tokens,
            len(prompt.split()),
            len(prompt),
            False,
        )
        return GarmentPromptOutcome(prompt=prompt, openai_analysis_success=True, fallback_used=False)
    except Exception as exc:  # pragma: no cover - external provider fallback
        logger.warning("premium-prompt fallback category=%s error=%s", user_category, exc)
        logger.info(
            'PREMIUM_PROMPT_DEBUG generated_prompt="%s" response_ms=%s total_tokens=%s output_words=%s output_chars=%s fallback_used=%s',
            fallback_prompt,
            0,
            0,
            len(fallback_prompt.split()),
            len(fallback_prompt),
            True,
        )
        return GarmentPromptOutcome(prompt=fallback_prompt, openai_analysis_success=False, fallback_used=True)


def fallback_prompt_for_category(user_category: str) -> str:
    normalized = normalize_category(user_category)
    mapping = {
        "hoodie": "Oversized black hoodie, thick cotton, relaxed fit, front print preserved.",
        "hanorac": "Oversized black hoodie, thick cotton, relaxed fit, front print preserved.",
        "dress": "Long black fitted dress, flowing fabric, natural folds.",
        "rochie": "Long black fitted dress, flowing fabric, natural folds.",
        "jeans": "Baggy blue denim jeans, visible seams, medium wash, realistic folds.",
        "blugi": "Baggy blue denim jeans, visible seams, medium wash, realistic folds.",
        "tshirt": "Structured cotton t-shirt, clean hem seams, soft texture.",
        "tricou": "Structured cotton t-shirt, clean hem seams, soft texture.",
        "shirt": "Button-up shirt, crisp collar, light drape, sleeve folds.",
        "camasa": "Button-up shirt, crisp collar, light drape, sleeve folds.",
        "cămașă": "Button-up shirt, crisp collar, light drape, sleeve folds.",
        "jacket": "Black jacket, structured fit, zipper details, textured outer layer.",
        "geaca": "Black jacket, structured fit, zipper details, textured outer layer.",
        "geacă": "Black jacket, structured fit, zipper details, textured outer layer.",
    }
    return mapping.get(
        normalized,
        "Realistic garment, accurate fit, visible seams, natural folds, fabric texture.",
    )


def _sanitize_prompt(value: str) -> str:
    cleaned = value.replace("\n", " ").strip().strip('"').strip("'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\bwith\b", ",", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\band\b", ",", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[.;:]+", ",", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned).strip(" ,")
    segments = [segment.strip() for segment in cleaned.split(",") if segment.strip()]
    normalized_segments: list[str] = []
    for segment in segments:
        segment = re.sub(r"^(this|that|the|a|an)\s+", "", segment, flags=re.IGNORECASE)
        segment = re.sub(r"\b(realistic|important|visible)\b\s+", "", segment, flags=re.IGNORECASE)
        segment = re.sub(r"\bpreservation\b", "preserved", segment, flags=re.IGNORECASE)
        normalized_segments.append(segment)

    cleaned = ", ".join(normalized_segments)
    words = cleaned.split()
    if len(words) > 25:
        cleaned = " ".join(words[:25]).rstrip(",")
        cleaned = re.sub(r"\s*,\s*[^,]*$", "", cleaned).strip(" ,") or " ".join(words[:18]).strip(" ,")
    return cleaned
