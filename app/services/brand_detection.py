from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.brand import Brand

CATEGORY_KEYWORDS = {
    "hoodie": ["hoodie", "hanorac", "sweatshirt"],
    "tshirt": ["t-shirt", "tshirt", "tee", "tricou"],
    "pants": ["pants", "trousers", "joggers", "pantaloni"],
    "jeans": ["jeans", "denim", "blugi"],
}


def detect_brand_candidates(
    db: Session,
    filename: str,
    brand_hint_text: Optional[str] = None,
    category_hint_text: Optional[str] = None,
) -> Tuple[List[dict], Optional[str], str]:
    brands = db.query(Brand).filter(Brand.active.is_(True)).all()
    normalized_text = normalize_text(" ".join(filter(None, [filename, brand_hint_text, category_hint_text])))
    candidate_brands = []

    if normalized_text:
        for brand in brands:
            score = _score_brand_match(brand, normalized_text)
            if score <= 0:
                continue
            candidate_brands.append(
                {
                    "brand_id": brand.id,
                    "brand_name": brand.name,
                    "confidence": round(score, 3),
                }
            )

    candidate_brands.sort(key=lambda item: item["confidence"], reverse=True)
    category_code = detect_category_code(normalized_text)
    return candidate_brands[:3], category_code, normalized_text


def normalize_text(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9& ]+", " ", value or "")
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def detect_category_code(normalized_text: str) -> Optional[str]:
    for category_code, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in normalized_text for keyword in keywords):
            return category_code
    return None


def _score_brand_match(brand: Brand, normalized_text: str) -> float:
    aliases = [brand.name, brand.slug.replace("-", " ")] + list(brand.aliases_json or [])
    normalized_aliases = [normalize_text(alias) for alias in aliases if alias]

    best_score = 0.0
    tokens = normalized_text.split()
    for alias in normalized_aliases:
        if not alias:
            continue
        if alias in normalized_text:
            best_score = max(best_score, 0.97 if alias == normalize_text(brand.name) else 0.9)
            continue
        for token in tokens:
            best_score = max(best_score, SequenceMatcher(None, alias, token).ratio() * 0.75)

    return best_score if best_score >= 0.55 else 0.0

