from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GenerationTier(str, Enum):
    standard = "standard"
    premium = "premium"


STANDARD_TRYON_COST = 1
PREMIUM_TRYON_COST = 2
STANDARD_FASHN_MODEL = "tryon-v1.6"
PREMIUM_FASHN_MODEL = "tryon-max"
FORCED_PREMIUM_CATEGORIES = {"dress", "rochie"}
STANDARD_ALLOWED_FASHN_CATEGORIES = {"tops", "bottoms"}
PREMIUM_ALLOWED_FASHN_CATEGORIES = {"tops", "bottoms", "one-pieces", "auto"}
PREMIUM_RECOMMENDED_CATEGORIES = {
    "oversized",
    "layered outfit",
    "blazer",
    "suit",
    "transparent material",
    "shiny material",
    "patterned clothes",
    "long coat",
    "full outfit",
}


@dataclass(frozen=True)
class TryOnRoutingDecision:
    user_category: str
    requested_generation_tier: GenerationTier
    final_generation_tier: GenerationTier
    fashn_model_name: str
    credits_required: int
    requires_openai_analysis: bool
    force_premium: bool
    standard_category: str
    premium_category: str
    premium_recommended: bool


def resolve_tryon_routing(
    *,
    user_category: str,
    requested_generation_tier: str | None,
) -> TryOnRoutingDecision:
    normalized_category = normalize_category(user_category)
    requested_tier = parse_generation_tier(requested_generation_tier)
    force_premium = normalized_category in FORCED_PREMIUM_CATEGORIES
    premium_recommended = normalized_category in PREMIUM_RECOMMENDED_CATEGORIES
    premium_category = map_premium_category(normalized_category)

    final_tier = GenerationTier.premium if force_premium else requested_tier
    if final_tier == GenerationTier.premium:
        return TryOnRoutingDecision(
            user_category=normalized_category,
            requested_generation_tier=requested_tier,
            final_generation_tier=final_tier,
            fashn_model_name=PREMIUM_FASHN_MODEL,
            credits_required=PREMIUM_TRYON_COST,
            requires_openai_analysis=True,
            force_premium=force_premium,
            standard_category=_safe_standard_category(normalized_category),
            premium_category=premium_category,
            premium_recommended=premium_recommended or force_premium,
        )

    standard_category = map_standard_category(normalized_category)
    return TryOnRoutingDecision(
        user_category=normalized_category,
        requested_generation_tier=requested_tier,
        final_generation_tier=GenerationTier.standard,
        fashn_model_name=STANDARD_FASHN_MODEL,
        credits_required=STANDARD_TRYON_COST,
        requires_openai_analysis=False,
        force_premium=False,
        standard_category=standard_category,
        premium_category=premium_category,
        premium_recommended=premium_recommended,
    )


def parse_generation_tier(value: str | None) -> GenerationTier:
    normalized = (value or "").strip().lower()
    if normalized == GenerationTier.premium.value:
        return GenerationTier.premium
    return GenerationTier.standard


def normalize_category(category: str | None) -> str:
    return (category or "").strip().lower()


def map_standard_category(user_category: str) -> str:
    mapping = {
        "tricou": "tops",
        "camasa": "tops",
        "cămașă": "tops",
        "geaca": "tops",
        "geacă": "tops",
        "hanorac": "tops",
        "bluza": "tops",
        "bluză": "tops",
        "pulover": "tops",
        "palton": "tops",
        "top": "tops",
        "tshirt": "tops",
        "t-shirt": "tops",
        "shirt": "tops",
        "blouse": "tops",
        "hoodie": "tops",
        "sweater": "tops",
        "jacket": "tops",
        "coat": "tops",
        "blazer": "tops",
        "suit": "tops",
        "blugi": "bottoms",
        "pantaloni": "bottoms",
        "fusta": "bottoms",
        "fustă": "bottoms",
        "shorts": "bottoms",
        "jeans": "bottoms",
        "pants": "bottoms",
        "skirt": "bottoms",
        "bottom": "bottoms",
    }
    normalized = normalize_category(user_category)
    mapped = mapping.get(normalized)
    if mapped is None:
        raise ValueError(f"Unsupported Standard category: {user_category}")
    return validate_standard_category(mapped)


def map_premium_category(user_category: str) -> str:
    normalized = normalize_category(user_category)
    if normalized in FORCED_PREMIUM_CATEGORIES:
        return "one-pieces"
    try:
        return map_standard_category(normalized)
    except ValueError:
        return validate_premium_category("auto")


def validate_standard_category(category: str) -> str:
    if category not in STANDARD_ALLOWED_FASHN_CATEGORIES:
        raise ValueError(f"Invalid Standard FASHN category: {category}")
    return category


def validate_premium_category(category: str) -> str:
    if category not in PREMIUM_ALLOWED_FASHN_CATEGORIES:
        raise ValueError(f"Invalid Premium FASHN category: {category}")
    return category


def _safe_standard_category(user_category: str) -> str:
    try:
        return map_standard_category(user_category)
    except ValueError:
        return "tops"
