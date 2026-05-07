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
    "You extract non-color garment details for AI try-on reconstruction using compact comma-separated descriptors. "
    "Never mention colors."
)
USER_PROMPT = (
    "Describe only non-color reconstruction details of this garment using short comma-separated descriptors. "
    "Mention fit, material, texture, seams, folds and print/logo details. "
    "Do not mention color or garment type. Maximum 12 words."
)
COLOR_WORD_PATTERNS = [
    r"\bblack\b",
    r"\bwhite\b",
    r"\bblue\b",
    r"\bred\b",
    r"\bgreen\b",
    r"\byellow\b",
    r"\borange\b",
    r"\bpurple\b",
    r"\bpink\b",
    r"\bbrown\b",
    r"\bbeige\b",
    r"\bgrey\b",
    r"\bgray\b",
    r"\bnavy\b",
    r"\bcream\b",
    r"\bgold\b",
    r"\bsilver\b",
    r"\bmaroon\b",
    r"\bburgundy\b",
    r"\bkhaki\b",
    r"\btan\b",
    r"\bteal\b",
    r"\bturquoise\b",
    r"\bindigo\b",
    r"\bmulticolor\b",
    r"\bmulticolored\b",
    r"\bneon\b",
    r"\bwashed\b",
    r"\blight wash\b",
    r"\bmedium wash\b",
    r"\bdark wash\b",
    r"\bnegru\b",
    r"\balb\b",
    r"\balba(?:str[ăa])?\b",
    r"\bro[sș]u\b",
    r"\bverde\b",
    r"\bgalben\b",
    r"\bportocaliu\b",
    r"\bmov\b",
    r"\broz\b",
    r"\bmaro\b",
    r"\bbej\b",
    r"\bgris?\b",
    r"\bgri\b",
    r"\bbleumarin\b",
    r"\bcrem\b",
    r"\bauriu\b",
    r"\bargintiu\b",
    r"\bvi[sș]iniu\b",
    r"\bkhaki\b",
]
GARMENT_TYPE_PATTERNS = [
    r"\bhoodie\b",
    r"\bdress\b",
    r"\bjeans\b",
    r"\bpants\b",
    r"\bskirt\b",
    r"\bjacket\b",
    r"\bshirt\b",
    r"\bt-?shirt\b",
    r"\bblouse\b",
    r"\bsweater\b",
    r"\bcoat\b",
    r"\btop\b",
]

@dataclass(frozen=True)
class GarmentPromptOutcome:
    prompt: str
    openai_analysis_success: bool
    fallback_used: bool


def generate_premium_garment_prompt(
    image_reference: str,
    user_category: str,
    *,
    user_selected_color: str | None = None,
) -> GarmentPromptOutcome:
    fallback_prompt = fallback_prompt_for_category(user_category, user_selected_color=user_selected_color)
    if not get_openai_api_key():
        logger.warning("OPENAI_API_KEY is not configured. Using premium fallback prompt.")
        logger.info(
            'PREMIUM_PROMPT_DEBUG category=%s generated_prompt="%s" response_ms=%s total_tokens=%s output_words=%s output_chars=%s user_selected_color=%s fallback_used=%s',
            normalize_category(user_category) or "unknown",
            fallback_prompt,
            0,
            0,
            len(fallback_prompt.split()),
            len(fallback_prompt),
            _clean_user_selected_color(user_selected_color) or "",
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
                            "text": (
                                f"{USER_PROMPT} "
                                f"Category hint: {_prompt_category_label(user_category)}. "
                                f"User-selected color: {_clean_user_selected_color(user_selected_color) or 'none'}."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_reference}},
                    ],
                },
            ],
            max_tokens=40,
            temperature=0,
        )
        detail_descriptors = _sanitize_descriptors(response.choices[0].message.content or "")
        if not detail_descriptors:
            raise RuntimeError("OpenAI returned an empty premium garment prompt.")
        prompt = _compose_premium_prompt(
            user_category,
            detail_descriptors,
            user_selected_color=user_selected_color,
        )

        response_ms = int((time.monotonic() - started_at) * 1000)
        total_tokens = getattr(response.usage, "total_tokens", 0) if getattr(response, "usage", None) else 0
        logger.info("premium-prompt success category=%s prompt=%s", user_category, prompt)
        logger.info(
            'PREMIUM_PROMPT_DEBUG category=%s generated_prompt="%s" response_ms=%s total_tokens=%s output_words=%s output_chars=%s user_selected_color=%s fallback_used=%s',
            normalize_category(user_category) or "unknown",
            prompt,
            response_ms,
            total_tokens,
            len(prompt.split()),
            len(prompt),
            _clean_user_selected_color(user_selected_color) or "",
            False,
        )
        return GarmentPromptOutcome(prompt=prompt, openai_analysis_success=True, fallback_used=False)
    except Exception as exc:  # pragma: no cover - external provider fallback
        logger.warning(
            "premium-prompt fallback category=%s error_type=%s",
            user_category,
            type(exc).__name__,
        )
        logger.info(
            'PREMIUM_PROMPT_DEBUG category=%s generated_prompt="%s" response_ms=%s total_tokens=%s output_words=%s output_chars=%s user_selected_color=%s fallback_used=%s',
            normalize_category(user_category) or "unknown",
            fallback_prompt,
            0,
            0,
            len(fallback_prompt.split()),
            len(fallback_prompt),
            _clean_user_selected_color(user_selected_color) or "",
            True,
        )
        return GarmentPromptOutcome(prompt=fallback_prompt, openai_analysis_success=False, fallback_used=True)


def fallback_prompt_for_category(user_category: str, *, user_selected_color: str | None = None) -> str:
    detail_descriptors = fallback_details_for_category(user_category)
    return _compose_premium_prompt(
        user_category,
        detail_descriptors,
        user_selected_color=user_selected_color,
    )


def fallback_details_for_category(user_category: str) -> str:
    normalized = normalize_category(user_category)
    mapping = {
        "hoodie": "thick fabric, relaxed fit, natural folds, front print preserved",
        "hanorac": "thick fabric, relaxed fit, natural folds, front print preserved",
        "dress": "soft drape, fitted shape, natural folds",
        "rochie": "soft drape, fitted shape, natural folds",
        "jeans": "visible seams, denim texture, natural folds",
        "blugi": "visible seams, denim texture, natural folds",
        "tshirt": "clean hem seams, soft fabric texture",
        "tricou": "clean hem seams, soft fabric texture",
        "shirt": "crisp collar, light drape, sleeve folds",
        "camasa": "crisp collar, light drape, sleeve folds",
        "cămașă": "crisp collar, light drape, sleeve folds",
        "jacket": "structured fit, zipper details, textured outer layer",
        "geaca": "structured fit, zipper details, textured outer layer",
        "geacă": "structured fit, zipper details, textured outer layer",
    }
    return mapping.get(
        normalized,
        "visible seams, natural folds, fabric texture",
    )


def _sanitize_descriptors(value: str) -> str:
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
        segment = re.sub(r"\b(realistic|important)\b\s+", "", segment, flags=re.IGNORECASE)
        segment = re.sub(r"\bpreservation\b", "preserved", segment, flags=re.IGNORECASE)
        for pattern in COLOR_WORD_PATTERNS:
            segment = re.sub(pattern, "", segment, flags=re.IGNORECASE)
        for pattern in GARMENT_TYPE_PATTERNS:
            segment = re.sub(pattern, "", segment, flags=re.IGNORECASE)
        segment = re.sub(r"\s+", " ", segment)
        segment = re.sub(r"\s*,\s*", ", ", segment).strip(" ,")
        if not segment:
            continue
        normalized_segments.append(segment)

    cleaned = ", ".join(normalized_segments)
    words = cleaned.split()
    if len(words) > 12:
        cleaned = " ".join(words[:12]).rstrip(",")
        cleaned = re.sub(r"\s*,\s*[^,]*$", "", cleaned).strip(" ,") or " ".join(words[:10]).strip(" ,")
    return cleaned


def _compose_premium_prompt(
    user_category: str,
    detail_descriptors: str,
    *,
    user_selected_color: str | None = None,
) -> str:
    garment_label = _prompt_category_label(user_category)
    selected_color = _clean_user_selected_color(user_selected_color)
    base_segments = [f"realistic try-on of the selected {garment_label}"]
    if selected_color:
        base_segments.append(f"match the user-selected {selected_color} color exactly")
    else:
        base_segments.append("preserve the original garment color, texture, shape and fit")
        base_segments.append("do not recolor the garment")
    if detail_descriptors:
        base_segments.append(detail_descriptors)
    prompt = ", ".join(segment for segment in base_segments if segment)
    return _truncate_prompt(prompt, max_words=28)


def _prompt_category_label(user_category: str) -> str:
    normalized = normalize_category(user_category)
    mapping = {
        "tricou": "t-shirt",
        "tshirt": "t-shirt",
        "t-shirt": "t-shirt",
        "camasa": "shirt",
        "cămașă": "shirt",
        "shirt": "shirt",
        "blouse": "blouse",
        "bluza": "blouse",
        "bluză": "blouse",
        "hoodie": "hoodie",
        "hanorac": "hoodie",
        "sweater": "sweater",
        "pulover": "sweater",
        "dress": "dress",
        "rochie": "dress",
        "jeans": "jeans",
        "blugi": "jeans",
        "pants": "pants",
        "pantaloni": "pants",
        "skirt": "skirt",
        "fusta": "skirt",
        "fustă": "skirt",
        "jacket": "jacket",
        "geaca": "jacket",
        "geacă": "jacket",
        "coat": "coat",
        "palton": "coat",
        "top": "top",
    }
    return mapping.get(normalized, normalized or "garment")


def _clean_user_selected_color(value: str | None) -> str | None:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return cleaned or None


def _truncate_prompt(value: str, *, max_words: int) -> str:
    words = value.split()
    if len(words) <= max_words:
        return value.strip(" ,")
    return " ".join(words[:max_words]).strip(" ,")
