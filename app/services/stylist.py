from __future__ import annotations

from typing import Dict, List, Sequence

from sqlalchemy.orm import Session

from app.models.brand import Brand
from app.models.category import Category
from app.models.user import User
from app.schemas.common import (
    StylistBrandSuggestion,
    StylistCategorySuggestion,
    StylistOutfitIdea,
    StylistRecommendationRequest,
    StylistRecommendationResponse,
    StylistSizeSuggestion,
)
from app.services.fit import suggest_best_size

COLOR_KEYWORDS = [
    "black",
    "white",
    "grey",
    "gray",
    "navy",
    "blue",
    "beige",
    "brown",
    "green",
    "olive",
    "cream",
    "pink",
    "red",
    "burgundy",
]

NEUTRAL_COLORS = {"black", "white", "grey", "gray", "navy", "beige", "cream"}

COLOR_FAMILY_MAP = {
    "black": "neutral",
    "white": "neutral",
    "grey": "neutral",
    "gray": "neutral",
    "navy": "cool",
    "blue": "cool",
    "beige": "warm",
    "brown": "warm",
    "green": "earthy",
    "olive": "earthy",
    "cream": "warm",
    "pink": "bright",
    "red": "bright",
    "burgundy": "rich",
}

COLOR_COMBINATION_NOTES = {
    frozenset({"black", "white"}): "Black and white is the safest high-contrast combination when you want the outfit to stay sharp.",
    frozenset({"beige", "navy"}): "Beige with navy reads polished and expensive without looking too formal.",
    frozenset({"olive", "cream"}): "Olive and cream gives a clean earthy palette that still feels soft on the eye.",
    frozenset({"blue", "white"}): "Blue with white stays fresh and easy to repeat across casual outfits.",
}

STYLE_KEYWORDS = {
    "streetwear": {"streetwear", "street", "urban", "oversized", "baggy"},
    "minimal": {"minimal", "clean", "simple", "neutral", "timeless"},
    "smart-casual": {"smart casual", "office", "smart", "work", "clean fit"},
    "athleisure": {"gym", "training", "workout", "sport", "running", "athleisure"},
    "casual": {"casual", "daily", "everyday", "basic", "relaxed"},
}

FIT_KEYWORDS = {
    "oversized": {"oversized", "oversize", "boxy"},
    "regular": {"regular", "classic", "standard"},
    "slim": {"slim", "fitted", "tailored"},
    "skinny": {"skinny"},
    "relaxed": {"relaxed", "easy fit"},
    "straight": {"straight", "straight leg"},
    "wide": {"wide", "wide leg", "wide-leg"},
    "baggy": {"baggy", "loose"},
}

FIT_TERM_ORDER = ["oversized", "regular", "slim", "skinny", "relaxed", "straight", "wide", "baggy"]
TOP_RELEVANT_FITS = {"oversized", "regular", "slim", "relaxed", "baggy"}
BOTTOM_RELEVANT_FITS = {"regular", "slim", "skinny", "relaxed", "straight", "wide", "baggy"}

TOP_CATEGORY_CODES = {"tshirt", "hoodie"}
BOTTOM_CATEGORY_CODES = {"pants", "jeans"}
TOP_GARMENT_WORDS = {"top", "tee", "tshirt", "t shirt", "shirt", "hoodie", "sweatshirt", "overshirt"}
BOTTOM_GARMENT_WORDS = {"bottom", "pants", "jeans", "trousers", "denim", "cargo", "joggers"}

DEFAULT_STYLE_ORDER = ["casual", "minimal"]

BRAND_STYLE_MAP: Dict[str, Dict[str, object]] = {
    "zara": {
        "budget": "mid",
        "aesthetic": "clean smart-casual",
        "style_tags": {"smart-casual", "minimal", "casual"},
        "strengths": {"pants", "jeans", "tshirt"},
    },
    "nike": {
        "budget": "mid",
        "aesthetic": "sporty streetwear",
        "style_tags": {"athleisure", "streetwear", "casual"},
        "strengths": {"hoodie", "tshirt", "pants"},
    },
    "h-and-m": {
        "budget": "budget",
        "aesthetic": "accessible basics",
        "style_tags": {"minimal", "casual", "smart-casual"},
        "strengths": {"tshirt", "hoodie", "pants"},
    },
    "bershka": {
        "budget": "budget",
        "aesthetic": "trend-driven streetwear",
        "style_tags": {"streetwear", "casual"},
        "strengths": {"hoodie", "jeans", "tshirt"},
    },
    "pull-and-bear": {
        "budget": "budget",
        "aesthetic": "young casual streetwear",
        "style_tags": {"streetwear", "casual", "minimal"},
        "strengths": {"hoodie", "jeans", "pants"},
    },
}

BUDGET_RANK = {"budget": 0, "mid": 1, "premium": 2}

CATEGORY_REASON_MAP = {
    "tshirt": "A clean tee gives the outfit a reliable base and is easy to style across brands.",
    "hoodie": "A hoodie works well when the request leans relaxed, sporty, or oversized.",
    "pants": "Structured pants help anchor smart-casual and minimal outfits.",
    "jeans": "Jeans are a safe everyday option when you want something versatile and easy to search for.",
}

STYLE_CATEGORY_ORDER = {
    "streetwear": ["hoodie", "jeans", "tshirt", "pants"],
    "minimal": ["tshirt", "pants", "jeans", "hoodie"],
    "smart-casual": ["pants", "tshirt", "jeans"],
    "athleisure": ["hoodie", "pants", "tshirt"],
    "casual": ["tshirt", "jeans", "hoodie", "pants"],
}


def build_stylist_recommendation(
    db: Session,
    user: User,
    payload: StylistRecommendationRequest,
) -> StylistRecommendationResponse:
    brands = db.query(Brand).filter(Brand.active.is_(True)).all()
    categories = db.query(Category).all()
    category_by_code = {category.code: category for category in categories}

    detected_styles = _detect_style_tags(payload)
    colors = _detect_colors(payload)
    preferred_brand_slugs = {_normalize_slug(value) for value in payload.preferred_brands}
    excluded_brand_slugs = {_normalize_slug(value) for value in payload.excluded_brands}

    ranked_brands = _rank_brands(
        brands=brands,
        detected_styles=detected_styles,
        budget_level=(payload.budget_level or "mid").lower(),
        preferred_brand_slugs=preferred_brand_slugs,
        excluded_brand_slugs=excluded_brand_slugs,
    )
    selected_brands = ranked_brands[:3]

    category_codes = _pick_categories(payload.prompt, detected_styles, category_by_code)
    selected_categories = [category_by_code[code] for code in category_codes if code in category_by_code]
    fit_terms = _detect_fit_terms(payload.prompt, category_codes, user)

    search_queries = _build_search_queries(selected_brands, category_codes, colors, user, fit_terms)
    outfit_ideas = _build_outfit_ideas(selected_brands, category_codes, colors, detected_styles, fit_terms)
    fit_notes, warnings = _build_fit_notes(user, category_codes, colors, detected_styles, fit_terms)

    size_suggestions: List[StylistSizeSuggestion] = []
    if payload.include_size_suggestions:
        size_suggestions = _build_size_suggestions(db, user, selected_brands, selected_categories, warnings)

    summary = _build_summary(
        selected_brands,
        category_codes,
        detected_styles,
        colors,
        bool(size_suggestions),
        fit_terms,
    )

    return StylistRecommendationResponse(
        summary=summary,
        detected_style_tags=detected_styles,
        recommended_colors=colors,
        search_queries=search_queries,
        suggested_categories=[
            StylistCategorySuggestion(
                category_code=category.code,
                label=category.name,
                reason=CATEGORY_REASON_MAP.get(category.code, "This category matches the overall brief."),
            )
            for category in selected_categories
        ],
        suggested_brands=[
            StylistBrandSuggestion(
                brand_id=brand.id,
                brand_name=brand.name,
                reason=_brand_reason(brand.slug, detected_styles),
                aesthetic=str(BRAND_STYLE_MAP.get(brand.slug, {}).get("aesthetic", "versatile everyday basics")),
            )
            for brand in selected_brands
        ],
        size_suggestions=size_suggestions,
        outfit_ideas=outfit_ideas,
        fit_notes=fit_notes,
        warnings=warnings,
    )


def _detect_style_tags(payload: StylistRecommendationRequest) -> List[str]:
    haystack = " ".join(
        value
        for value in [payload.prompt, payload.occasion or "", payload.season or ""]
        if value
    ).lower()

    detected = [style for style, keywords in STYLE_KEYWORDS.items() if any(keyword in haystack for keyword in keywords)]
    if not detected:
        return DEFAULT_STYLE_ORDER.copy()
    if "casual" not in detected:
        detected.append("casual")
    return detected[:3]


def _detect_colors(payload: StylistRecommendationRequest) -> List[str]:
    colors = [color.lower() for color in payload.preferred_colors if color]
    haystack = payload.prompt.lower()

    for color in COLOR_KEYWORDS:
        if color in haystack and color not in colors:
            colors.append(color)

    if not colors:
        colors = ["black", "white", "navy"]
    return colors[:3]


def _detect_fit_terms(prompt: str, category_codes: Sequence[str], user: User) -> Dict[str, List[str]]:
    normalized_prompt = _normalize_phrase(prompt)
    top_terms: List[str] = []
    bottom_terms: List[str] = []
    general_terms: List[str] = []

    for term in FIT_TERM_ORDER:
        variants = {_normalize_phrase(value) for value in FIT_KEYWORDS[term]}
        if not any(_phrase_in_text(normalized_prompt, variant) for variant in variants):
            continue

        matched_top = any(_fit_phrase_matches_context(normalized_prompt, variant, TOP_GARMENT_WORDS) for variant in variants)
        matched_bottom = any(_fit_phrase_matches_context(normalized_prompt, variant, BOTTOM_GARMENT_WORDS) for variant in variants)

        if matched_top and term not in top_terms:
            top_terms.append(term)
        if matched_bottom and term not in bottom_terms:
            bottom_terms.append(term)
        if not matched_top and not matched_bottom and term not in general_terms:
            general_terms.append(term)

    profile_fit = ""
    if user.body_profile and user.body_profile.fit_preference:
        profile_fit = user.body_profile.fit_preference.lower().strip()

    if profile_fit in FIT_KEYWORDS and profile_fit not in general_terms and profile_fit not in top_terms and profile_fit not in bottom_terms:
        general_terms.append(profile_fit)

    has_top_category = any(code in TOP_CATEGORY_CODES for code in category_codes)
    has_bottom_category = any(code in BOTTOM_CATEGORY_CODES for code in category_codes)

    if has_top_category and not top_terms:
        top_default = _first_relevant_term(general_terms, TOP_RELEVANT_FITS)
        if top_default:
            top_terms.append(top_default)

    if has_bottom_category and not bottom_terms:
        bottom_default = _first_relevant_term(general_terms, BOTTOM_RELEVANT_FITS)
        if bottom_default:
            bottom_terms.append(bottom_default)

    return {
        "top": _ordered_unique(top_terms),
        "bottom": _ordered_unique(bottom_terms),
        "general": _ordered_unique(general_terms),
    }


def _rank_brands(
    brands: Sequence[Brand],
    detected_styles: Sequence[str],
    budget_level: str,
    preferred_brand_slugs: set[str],
    excluded_brand_slugs: set[str],
) -> List[Brand]:
    target_budget_rank = BUDGET_RANK.get(budget_level, 1)
    scored: List[tuple[float, Brand]] = []

    for brand in brands:
        if brand.slug in excluded_brand_slugs:
            continue

        metadata = BRAND_STYLE_MAP.get(brand.slug, {})
        style_tags = set(metadata.get("style_tags", {"casual"}))
        budget_rank = BUDGET_RANK.get(str(metadata.get("budget", "mid")), 1)

        score = 1.0
        score += len(style_tags.intersection(detected_styles)) * 1.4
        score += max(0, 1.0 - abs(target_budget_rank - budget_rank) * 0.5)
        if brand.slug in preferred_brand_slugs:
            score += 2.0

        scored.append((score, brand))

    scored.sort(key=lambda item: (item[0], item[1].name.lower()), reverse=True)
    return [brand for _, brand in scored]


def _pick_categories(prompt: str, detected_styles: Sequence[str], category_by_code: Dict[str, Category]) -> List[str]:
    prompt_lower = prompt.lower()
    explicit_matches = [
        code for code in category_by_code
        if code in prompt_lower or category_by_code[code].name.lower() in prompt_lower
    ]
    if explicit_matches:
        return explicit_matches[:3]

    ordered_codes: List[str] = []
    for style in detected_styles:
        for code in STYLE_CATEGORY_ORDER.get(style, []):
            if code in category_by_code and code not in ordered_codes:
                ordered_codes.append(code)

    if not ordered_codes:
        ordered_codes = ["tshirt", "jeans"]
    return ordered_codes[:3]


def _build_search_queries(
    brands: Sequence[Brand],
    category_codes: Sequence[str],
    colors: Sequence[str],
    user: User,
    fit_terms: Dict[str, List[str]],
) -> List[str]:
    queries: List[str] = []
    if not category_codes:
        return queries

    fallback_brand = brands[0].name if brands else "Your preferred brand"
    fallback_color = colors[0] if colors else "black"

    for index, category_code in enumerate(category_codes):
        brand_name = brands[index].name if index < len(brands) else fallback_brand
        color = colors[min(index, len(colors) - 1)] if colors else fallback_color
        fit_word = _search_fit_for_category(category_code, fit_terms, user)
        queries.append(f"{brand_name} {fit_word} {category_code} {color}".strip())

    while len(queries) < 3:
        category_code = category_codes[len(queries) % len(category_codes)]
        fit_word = _search_fit_for_category(category_code, fit_terms, user)
        query = f"{fallback_brand} {fit_word} {category_code} {fallback_color}".strip()
        if query not in queries:
            queries.append(query)
            continue
        break

    return queries[:3]


def _build_outfit_ideas(
    brands: Sequence[Brand],
    category_codes: Sequence[str],
    colors: Sequence[str],
    detected_styles: Sequence[str],
    fit_terms: Dict[str, List[str]],
) -> List[StylistOutfitIdea]:
    primary_brand = brands[0].name if brands else "Your preferred brand"
    accent_brand = brands[1].name if len(brands) > 1 else primary_brand
    primary_color = colors[0] if colors else "black"
    secondary_color = colors[1] if len(colors) > 1 else "white"
    lead_style = detected_styles[0] if detected_styles else "casual"

    top_category = next((code for code in category_codes if code in TOP_CATEGORY_CODES), "tshirt")
    bottom_category = next((code for code in category_codes if code in BOTTOM_CATEGORY_CODES), "jeans")
    top_shape = _outfit_fit_phrase(top_category, fit_terms)
    bottom_shape = _outfit_fit_phrase(bottom_category, fit_terms)

    ideas = [
        StylistOutfitIdea(
            title="Balanced daily look",
            summary="Keep one half of the outfit cleaner than the other so the silhouette stays intentional.",
            pieces=[
                f"{primary_brand} {primary_color} {top_shape} {top_category}",
                f"{accent_brand} {secondary_color} {bottom_shape} {bottom_category}",
                "Clean sneakers or low-profile trainers",
            ],
        ),
        StylistOutfitIdea(
            title="Safer fallback option",
            summary=f"A {lead_style} fallback built around easy colors and a shape that should work on repeat.",
            pieces=[
                f"{primary_brand} {primary_color} regular tshirt",
                f"{accent_brand} {secondary_color} straight jeans",
                "Simple layer or jacket in a neutral tone",
            ],
        ),
    ]
    return ideas


def _build_fit_notes(
    user: User,
    category_codes: Sequence[str],
    colors: Sequence[str],
    detected_styles: Sequence[str],
    fit_terms: Dict[str, List[str]],
) -> tuple[List[str], List[str]]:
    profile = user.body_profile
    fit_notes: List[str] = []
    warnings: List[str] = []

    if not profile:
        fit_notes.append("Add your body measurements to unlock accurate size guidance across brands.")
        warnings.append("Body profile is missing, so fit advice is based on styling rules instead of exact measurements.")
    else:
        fit_notes.append(f"Saved fit preference: {profile.fit_preference}.")
        missing = []
        for label, attr in (
            ("chest", profile.chest_cm),
            ("waist", profile.waist_cm),
            ("hips", profile.hips_cm),
        ):
            if attr is None:
                missing.append(label)

        if missing:
            warnings.append(f"Add {', '.join(missing)} to improve category-specific sizing confidence.")
        else:
            fit_notes.append("Chest, waist, and hips are saved, so cross-brand sizing should be more reliable.")

    silhouette_notes, silhouette_warnings = _build_silhouette_notes(fit_terms, category_codes, detected_styles, user)
    color_notes, color_warnings = _build_color_notes(colors)

    fit_notes.extend(silhouette_notes)
    fit_notes.extend(color_notes)
    warnings.extend(silhouette_warnings)
    warnings.extend(color_warnings)

    return _ordered_unique(fit_notes), _ordered_unique(warnings)


def _build_silhouette_notes(
    fit_terms: Dict[str, List[str]],
    category_codes: Sequence[str],
    detected_styles: Sequence[str],
    user: User,
) -> tuple[List[str], List[str]]:
    top_terms = set(fit_terms.get("top", []))
    bottom_terms = set(fit_terms.get("bottom", []))
    has_top = any(code in TOP_CATEGORY_CODES for code in category_codes)
    has_bottom = any(code in BOTTOM_CATEGORY_CODES for code in category_codes)
    lead_style = detected_styles[0] if detected_styles else "casual"

    notes: List[str] = []
    warnings: List[str] = []

    if "oversized" in top_terms and bottom_terms.intersection({"skinny", "slim"}):
        notes.append("If you keep the top oversized, straight or relaxed denim usually balances the outfit better than skinny bottoms.")
        warnings.append("An oversized top with skinny jeans can make the silhouette feel top-heavy and dated.")
    elif "oversized" in top_terms and bottom_terms.intersection({"wide", "baggy", "relaxed"}):
        notes.append("Oversized on top with wide or baggy pants can work for streetwear when one piece still has structure.")
        warnings.append("If both pieces are very loose, add structure with a tuck, shorter hem, or cleaner shoes so the outfit does not feel shapeless.")
    elif top_terms.intersection({"slim", "regular"}) and bottom_terms.intersection({"wide", "relaxed", "baggy"}):
        notes.append("A clean top with wider pants is usually the easiest way to keep the silhouette balanced because the volume sits on one half.")
    elif top_terms.intersection({"slim"}) and bottom_terms.intersection({"skinny", "slim"}):
        notes.append("Slim on top and skinny on the bottom looks sharp, but it creates a very fitted head-to-toe shape.")
        warnings.append("If you want a more current silhouette, swap the jeans for a straight or relaxed leg.")
    elif top_terms.intersection({"regular"}) and bottom_terms.intersection({"regular", "straight"}):
        notes.append("Regular tops with straight or regular bottoms are the safest everyday combination when you want something versatile.")

    if has_top and "oversized" in top_terms and not bottom_terms:
        notes.append("An oversized top usually pairs best with straight or relaxed bottoms rather than skinny denim.")
    if has_bottom and "skinny" in bottom_terms and not top_terms:
        notes.append("Skinny jeans work better with a clean regular or slim top than with a very oversized upper half.")
    if has_bottom and bottom_terms.intersection({"wide", "relaxed", "baggy"}) and not top_terms:
        notes.append("Wide or relaxed pants usually look cleaner when the top stays regular, slim, or lightly cropped.")

    if not notes:
        profile_fit = (user.body_profile.fit_preference.lower() if user.body_profile and user.body_profile.fit_preference else "regular")
        if profile_fit == "oversized":
            notes.append("Because your saved preference leans oversized, keep the other half of the outfit cleaner so the volume does not stack everywhere.")
        elif profile_fit == "slim":
            notes.append("Because your saved preference leans slim, straight or relaxed bottoms usually keep the outfit more current than going tight everywhere.")
        elif profile_fit == "regular":
            notes.append("Regular fits are the safest place to start, then push volume on only one piece if you want more personality.")
        elif lead_style == "streetwear":
            notes.append("Streetwear usually looks strongest when one piece carries the volume and the rest of the outfit stays cleaner.")
        else:
            notes.append("Keep one half of the look cleaner than the other so the outfit feels balanced instead of random.")

    return notes, warnings


def _build_color_notes(colors: Sequence[str]) -> tuple[List[str], List[str]]:
    normalized_colors = [color.lower() for color in colors if color]
    primary_colors = normalized_colors[:2]
    if not primary_colors:
        return [], []

    notes: List[str] = []
    warnings: List[str] = []
    pair_key = frozenset(primary_colors)

    if pair_key in COLOR_COMBINATION_NOTES:
        notes.append(COLOR_COMBINATION_NOTES[pair_key])
    elif all(color in NEUTRAL_COLORS for color in primary_colors):
        notes.append("Your lead colors are neutral, so the outfit should feel cleaner and much easier to repeat.")
    elif any(color in NEUTRAL_COLORS for color in primary_colors):
        notes.append("Using one neutral with one accent color keeps the look easier to wear without making it flat.")
    elif _color_family(primary_colors[0]) == _color_family(primary_colors[1]):
        notes.append("The first two colors sit in a similar family, so the palette should feel cohesive instead of noisy.")

    if len(primary_colors) == 2 and not any(color in NEUTRAL_COLORS for color in primary_colors):
        if _color_family(primary_colors[0]) != _color_family(primary_colors[1]):
            warnings.append("If you mix two stronger colors, add a neutral shoe, layer, or denim wash so the outfit does not get too busy.")

    return notes, warnings


def _build_size_suggestions(
    db: Session,
    user: User,
    brands: Sequence[Brand],
    categories: Sequence[Category],
    warnings: List[str],
) -> List[StylistSizeSuggestion]:
    suggestions: List[StylistSizeSuggestion] = []
    if not user.body_profile:
        return suggestions

    for brand in brands[:2]:
        for category in categories[:2]:
            size_label, confidence_score, reasons, _chart = suggest_best_size(db, user, brand, category)
            if not size_label:
                continue
            suggestions.append(
                StylistSizeSuggestion(
                    brand_id=brand.id,
                    brand_name=brand.name,
                    category_code=category.code,
                    size_label=size_label,
                    confidence_score=confidence_score,
                    reasons=reasons[:3],
                )
            )

    if not suggestions and user.body_profile:
        warnings.append("Size suggestions could not be generated because no compatible brand charts were found.")
    return suggestions[:4]


def _build_summary(
    brands: Sequence[Brand],
    category_codes: Sequence[str],
    detected_styles: Sequence[str],
    colors: Sequence[str],
    has_sizes: bool,
    fit_terms: Dict[str, List[str]],
) -> str:
    brand_names = ", ".join(brand.name for brand in brands[:2]) or "available brands"
    category_summary = ", ".join(category_codes[:2]) or "basics"
    style_summary = ", ".join(detected_styles[:2])
    color_summary = ", ".join(colors[:2])
    silhouette_summary = _summary_fit_line(fit_terms)
    sizing_suffix = " Size guidance is included." if has_sizes else " Add measurements to unlock size guidance."
    return (
        f"Built a {style_summary} direction around {category_summary} in {color_summary}, "
        f"with {brand_names} as the best starting point. {silhouette_summary}{sizing_suffix}"
    )


def _summary_fit_line(fit_terms: Dict[str, List[str]]) -> str:
    top_terms = set(fit_terms.get("top", []))
    bottom_terms = set(fit_terms.get("bottom", []))

    if "oversized" in top_terms and bottom_terms.intersection({"skinny", "slim"}):
        return "Swap skinny bottoms for straight or relaxed denim if you want the shape to feel more balanced."
    if "oversized" in top_terms and bottom_terms.intersection({"wide", "baggy", "relaxed"}):
        return "Let one loose piece lead and keep the rest a little cleaner so the outfit does not lose shape."
    if top_terms.intersection({"slim", "regular"}) and bottom_terms.intersection({"wide", "relaxed", "baggy"}):
        return "The silhouette already stays balanced because the volume sits mostly on the lower half."
    if "oversized" in top_terms:
        return "Let the oversized piece lead and keep the rest of the look cleaner so the outfit stays intentional."
    return "Keep volume on one half of the outfit and the whole look will be easier to wear."


def _brand_reason(brand_slug: str, detected_styles: Sequence[str]) -> str:
    metadata = BRAND_STYLE_MAP.get(brand_slug, {})
    strengths = sorted(metadata.get("strengths", {"basics"}))
    style_tags = sorted(metadata.get("style_tags", {"casual"}).intersection(detected_styles))
    strengths_text = ", ".join(strengths[:2])
    style_text = ", ".join(style_tags) if style_tags else "everyday wear"
    return f"Strong for {strengths_text} and lines up well with {style_text} requests."


def _search_fit_for_category(category_code: str, fit_terms: Dict[str, List[str]], user: User) -> str:
    top_terms = fit_terms.get("top", [])
    bottom_terms = fit_terms.get("bottom", [])
    general_terms = fit_terms.get("general", [])
    profile_fit = user.body_profile.fit_preference.lower() if user.body_profile and user.body_profile.fit_preference else "regular"

    if category_code in TOP_CATEGORY_CODES:
        return (
            _first_relevant_term(top_terms, TOP_RELEVANT_FITS)
            or ("regular" if "skinny" in bottom_terms else None)
            or _first_relevant_term(general_terms, TOP_RELEVANT_FITS)
            or (profile_fit if profile_fit in TOP_RELEVANT_FITS else "regular")
        )

    if category_code in BOTTOM_CATEGORY_CODES:
        return (
            _first_relevant_term(bottom_terms, BOTTOM_RELEVANT_FITS)
            or ("straight" if "oversized" in top_terms else None)
            or ("straight" if "slim" in top_terms else None)
            or _first_relevant_term(general_terms, BOTTOM_RELEVANT_FITS)
            or (profile_fit if profile_fit in BOTTOM_RELEVANT_FITS else "regular")
        )

    return _first_relevant_term(general_terms, FIT_TERM_ORDER) or profile_fit


def _outfit_fit_phrase(category_code: str, fit_terms: Dict[str, List[str]]) -> str:
    if category_code in TOP_CATEGORY_CODES:
        if "oversized" in fit_terms.get("top", []):
            return "oversized"
        if "slim" in fit_terms.get("top", []):
            return "clean slim"
        if "baggy" in fit_terms.get("top", []):
            return "relaxed"
        return "regular"

    if category_code in BOTTOM_CATEGORY_CODES:
        if "skinny" in fit_terms.get("bottom", []):
            return "skinny"
        if "wide" in fit_terms.get("bottom", []):
            return "wide-leg"
        if "baggy" in fit_terms.get("bottom", []) or "relaxed" in fit_terms.get("bottom", []):
            return "relaxed"
        if "straight" in fit_terms.get("bottom", []):
            return "straight"
        if "oversized" in fit_terms.get("top", []):
            return "straight"
        return "straight"

    return "clean"


def _fit_phrase_matches_context(prompt: str, fit_phrase: str, garment_words: set[str]) -> bool:
    return any(
        _phrase_in_text(prompt, f"{fit_phrase} {_normalize_phrase(garment_word)}")
        or _phrase_in_text(prompt, f"{_normalize_phrase(garment_word)} {fit_phrase}")
        for garment_word in garment_words
    )


def _phrase_in_text(text: str, phrase: str) -> bool:
    compact_text = f" {text} "
    compact_phrase = f" {phrase} "
    return compact_phrase in compact_text


def _normalize_phrase(value: str) -> str:
    return " ".join(value.lower().replace("-", " ").replace("/", " ").split())


def _color_family(color: str) -> str:
    return COLOR_FAMILY_MAP.get(color.lower(), "accent")


def _first_relevant_term(terms: Sequence[str], allowed_terms: Sequence[str] | set[str]) -> str | None:
    allowed = set(allowed_terms)
    for term in terms:
        if term in allowed:
            return term
    return None


def _ordered_unique(values: Sequence[str]) -> List[str]:
    ordered: List[str] = []
    for value in values:
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def _normalize_slug(value: str) -> str:
    return (
        value.lower()
        .replace("&", "and")
        .replace(" ", "-")
        .replace("_", "-")
        .strip("-")
    )
