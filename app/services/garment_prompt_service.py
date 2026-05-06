from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from openai import OpenAI

from app.core.config import settings
from app.services.tryon_routing import normalize_category

logger = logging.getLogger(__name__)

VISION_MODEL_NAME = "gpt-4o-mini"
SYSTEM_MESSAGE = (
    "You analyze garments for premium virtual try-on reconstruction. "
    "Return one concise, visual, reconstruction-focused description with realistic garment details only."
)
USER_PROMPT = (
    "Describe this garment in 15-40 words for premium virtual try-on reconstruction. "
    "Include garment type, color, fit, silhouette, fabric/material, texture, folds, seams/stitching, "
    "layering, sleeves, visible prints or logos, and realism-critical details. "
    "Be factual, concise, and reconstruction-focused. No marketing language."
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
                        {
                            "type": "text",
                            "text": f"{USER_PROMPT} Category hint: {normalize_category(user_category) or 'unknown'}.",
                        },
                        {"type": "image_url", "image_url": {"url": image_reference}},
                    ],
                },
            ],
            max_tokens=100,
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
        "hoodie": "Oversized black hoodie with thick cotton texture, dropped shoulders, relaxed fit, realistic folds and front print preservation.",
        "hanorac": "Oversized black hoodie with thick cotton texture, dropped shoulders, relaxed fit, realistic folds and front print preservation.",
        "dress": "Elegant long black dress with fitted waist, soft flowing fabric, realistic drape and natural fold movement.",
        "rochie": "Elegant long black dress with fitted waist, soft flowing fabric, realistic drape and natural fold movement.",
        "jeans": "Loose-fit blue denim jeans with medium wash texture, visible seam stitching, slightly baggy silhouette and realistic fabric folds.",
        "blugi": "Loose-fit blue denim jeans with medium wash texture, visible seam stitching, slightly baggy silhouette and realistic fabric folds.",
        "tshirt": "Structured t-shirt with soft cotton texture, clean sleeve shape, realistic hem stitching and natural fabric drape.",
        "tricou": "Structured t-shirt with soft cotton texture, clean sleeve shape, realistic hem stitching and natural fabric drape.",
        "shirt": "Button-up shirt with crisp collar structure, visible seam lines, light fabric drape and realistic sleeve folds.",
        "camasa": "Button-up shirt with crisp collar structure, visible seam lines, light fabric drape and realistic sleeve folds.",
        "cămașă": "Button-up shirt with crisp collar structure, visible seam lines, light fabric drape and realistic sleeve folds.",
        "jacket": "Black jacket with structured fit, visible zipper and seam details, realistic outerwear texture and preserved garment shape.",
        "geaca": "Black jacket with structured fit, visible zipper and seam details, realistic outerwear texture and preserved garment shape.",
        "geacă": "Black jacket with structured fit, visible zipper and seam details, realistic outerwear texture and preserved garment shape.",
    }
    return mapping.get(
        normalized,
        "Realistic clothing item with accurate fit, clear garment structure, visible seams, natural folds and believable fabric texture.",
    )


def _get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _sanitize_prompt(value: str) -> str:
    cleaned = value.strip().strip('"').strip("'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = cleaned.split()
    if len(words) > 40:
        cleaned = " ".join(words[:40])
    return cleaned
