from __future__ import annotations

import unicodedata
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

ROMANIAN_HINT_WORDS = {
    "vreau",
    "as",
    "aș",
    "ce",
    "cum",
    "merge",
    "potrivesc",
    "potriveste",
    "potrivește",
    "tinuta",
    "ținuta",
    "outfit",
    "haine",
    "haina",
    "jacheta",
    "jachetă",
    "pantofi",
    "tricou",
    "blugi",
    "pantaloni",
    "hanorac",
    "jeansi",
    "jeanși",
    "pentru",
    "ocazie",
    "office",
    "elegant",
    "casual",
    "marime",
    "mărime",
    "culoare",
    "culori",
    "negru",
    "alb",
    "bej",
    "bleumarin",
}

ROMANIAN_DIACRITICS = ("ă", "â", "î", "ș", "ş", "ț", "ţ")

COLOR_ALIASES = {
    "black": "black",
    "negru": "black",
    "noir": "black",
    "charcoal": "grey",
    "anthracite": "grey",
    "antracit": "grey",
    "white": "white",
    "alb": "white",
    "grey": "grey",
    "gray": "grey",
    "gri": "grey",
    "navy": "navy",
    "bleumarin": "navy",
    "blue": "blue",
    "albastru": "blue",
    "beige": "beige",
    "bej": "beige",
    "brown": "brown",
    "maro": "brown",
    "green": "green",
    "verde": "green",
    "olive": "olive",
    "oliv": "olive",
    "khaki": "olive",
    "masliniu": "olive",
    "măsliniu": "olive",
    "cream": "cream",
    "crem": "cream",
    "ivory": "cream",
    "ivoire": "cream",
    "pink": "pink",
    "roz": "pink",
    "red": "red",
    "rosu": "red",
    "roșu": "red",
    "burgundy": "burgundy",
    "bordo": "burgundy",
    "visiniu": "burgundy",
    "vișiniu": "burgundy",
    "camel": "beige",
    "taupe": "brown",
}

COLOR_KEYWORDS = list(COLOR_ALIASES.keys())
NEUTRAL_COLORS = {"black", "white", "grey", "navy", "beige", "cream"}

COLOR_FAMILY_MAP = {
    "black": "neutral",
    "white": "neutral",
    "grey": "neutral",
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
    frozenset({"black", "white"}): {
        "en": "Black and white is the safest high-contrast combination when you want the outfit to stay sharp.",
        "ro": "Negru cu alb este cea mai sigură combinație cu contrast mare când vrei o ținută clară și curată.",
    },
    frozenset({"beige", "navy"}): {
        "en": "Beige with navy reads polished and expensive without looking too formal.",
        "ro": "Bejul cu bleumarin arată elegant și premium fără să devină prea formal.",
    },
    frozenset({"olive", "cream"}): {
        "en": "Olive and cream gives a clean earthy palette that still feels soft on the eye.",
        "ro": "Olive cu crem dă o paletă pământie, curată și foarte plăcută vizual.",
    },
    frozenset({"blue", "white"}): {
        "en": "Blue with white stays fresh and easy to repeat across casual outfits.",
        "ro": "Albastrul cu alb rămâne fresh și ușor de repetat în ținute casual.",
    },
}

SEASON_KEYWORDS = {
    "spring": {"spring", "primavara", "primăvară"},
    "summer": {"summer", "vara", "vară"},
    "autumn": {"autumn", "fall", "toamna", "toamnă"},
    "winter": {"winter", "iarna", "iarnă"},
}

OCCASION_KEYWORDS = {
    "office": {"office", "work", "meeting", "business casual", "birou", "munca", "muncă", "intalnire", "întâlnire"},
    "evening": {"date", "date night", "dinner", "night out", "evening", "cina", "cină", "seara", "seară", "iesire", "ieșire"},
    "event": {"event", "party", "special occasion", "wedding", "nunta", "nuntă", "petrecere", "eveniment"},
    "travel": {"travel", "airport", "trip", "vacation", "city break", "calatorie", "călătorie", "vacanta", "vacanță"},
    "weekend": {"weekend", "coffee", "brunch", "walk", "shopping", "plimbare", "oras", "oraș"},
    "gym": {"gym", "training", "workout", "sport", "fitness", "sala", "sală", "antrenament"},
}

OCCASION_STYLE_HINTS = {
    "office": ["smart-casual", "minimal"],
    "evening": ["minimal", "smart-casual"],
    "event": ["smart-casual", "minimal"],
    "travel": ["casual", "athleisure"],
    "weekend": ["casual", "streetwear"],
    "gym": ["athleisure", "casual"],
}

SEASON_STYLE_HINTS = {
    "spring": ["casual", "minimal"],
    "summer": ["minimal", "casual"],
    "autumn": ["smart-casual", "streetwear"],
    "winter": ["streetwear", "smart-casual"],
}

STYLE_KEYWORDS = {
    "streetwear": {
        "streetwear",
        "street",
        "urban",
        "oversized",
        "baggy",
        "stradal",
        "strada",
        "oversize",
        "larg",
        "boxy",
    },
    "minimal": {
        "minimal",
        "clean",
        "simple",
        "neutral",
        "timeless",
        "luxury",
        "premium",
        "elevated",
        "curat",
        "simplu",
        "neutru",
        "atemporal",
        "monocrom",
    },
    "smart-casual": {
        "smart casual",
        "office",
        "smart",
        "work",
        "clean fit",
        "birou",
        "elegant",
        "officewear",
        "business casual",
        "meeting",
        "date night",
        "cina",
        "cină",
        "intalnire",
        "întâlnire",
    },
    "athleisure": {
        "gym",
        "training",
        "workout",
        "sport",
        "running",
        "athleisure",
        "sala",
        "sală",
        "antrenament",
        "sportiv",
        "alergare",
        "tennis",
        "tenis",
        "fitness",
    },
    "casual": {
        "casual",
        "daily",
        "everyday",
        "basic",
        "relaxed",
        "zilnic",
        "de zi cu zi",
        "lejer",
        "relaxat",
        "basic",
        "weekend",
        "travel",
        "vacanta",
        "vacanță",
        "plimbare",
    },
}

FIT_KEYWORDS = {
    "oversized": {"oversized", "oversize", "boxy", "larg", "lejer", "wide top", "croi lejer", "voluminos"},
    "regular": {"regular", "classic", "standard", "normal", "clasic", "regular fit", "croi normal", "croi clasic"},
    "slim": {"slim", "fitted", "tailored", "mulat", "cambrat", "stramt", "strâmt", "slim fit"},
    "skinny": {"skinny", "foarte stramt", "foarte strâmt", "super slim"},
    "relaxed": {"relaxed", "easy fit", "relaxat", "lejer", "loose fit"},
    "straight": {"straight", "straight leg", "straight-leg", "drept", "croi drept", "straight fit"},
    "wide": {"wide", "wide leg", "wide-leg", "largi", "croi larg", "wide fit"},
    "baggy": {"baggy", "loose", "foarte larg", "baggie", "baggy fit"},
}

FIT_TERM_ORDER = ["oversized", "regular", "slim", "skinny", "relaxed", "straight", "wide", "baggy"]
TOP_RELEVANT_FITS = {"oversized", "regular", "slim", "relaxed", "baggy"}
BOTTOM_RELEVANT_FITS = {"regular", "slim", "skinny", "relaxed", "straight", "wide", "baggy"}

TOP_CATEGORY_CODES = {"tshirt", "hoodie"}
BOTTOM_CATEGORY_CODES = {"pants", "jeans"}
TOP_GARMENT_WORDS = {
    "top",
    "tee",
    "tshirt",
    "t-shirt",
    "t shirt",
    "shirt",
    "hoodie",
    "sweatshirt",
    "overshirt",
    "tricou",
    "hanorac",
    "bluza",
    "bluză",
    "camasa",
    "cămașă",
}
BOTTOM_GARMENT_WORDS = {
    "bottom",
    "pants",
    "jeans",
    "trousers",
    "denim",
    "cargo",
    "joggers",
    "pantaloni",
    "blugi",
    "jeanși",
    "jeansi",
    "pantalon",
    "slacks",
}

CATEGORY_ALIASES = {
    "tshirt": {"tshirt", "t-shirt", "t shirt", "tee", "shirt", "tricou"},
    "hoodie": {"hoodie", "hanorac", "sweatshirt", "bluza cu gluga", "bluză cu glugă", "hanorac cu gluga", "hanorac cu glugă"},
    "pants": {"pants", "trousers", "pantaloni", "cargo", "joggers", "slacks"},
    "jeans": {"jeans", "denim", "blugi", "jeansi", "jeanși"},
}

DEFAULT_STYLE_ORDER = ["casual", "minimal"]

BRAND_STYLE_MAP: Dict[str, Dict[str, object]] = {
    "zara": {
        "budget": "mid",
        "aesthetic": {"en": "clean smart-casual", "ro": "smart-casual curat"},
        "style_tags": {"smart-casual", "minimal", "casual"},
        "strengths": {"pants", "jeans", "tshirt"},
    },
    "nike": {
        "budget": "mid",
        "aesthetic": {"en": "sporty streetwear", "ro": "streetwear sportiv"},
        "style_tags": {"athleisure", "streetwear", "casual"},
        "strengths": {"hoodie", "tshirt", "pants"},
    },
    "adidas": {
        "budget": "mid",
        "aesthetic": {"en": "clean sporty essentials", "ro": "esențiale sportive curate"},
        "style_tags": {"athleisure", "streetwear", "casual"},
        "strengths": {"hoodie", "pants", "tshirt"},
    },
    "h-and-m": {
        "budget": "budget",
        "aesthetic": {"en": "accessible basics", "ro": "basic-uri accesibile"},
        "style_tags": {"minimal", "casual", "smart-casual"},
        "strengths": {"tshirt", "hoodie", "pants"},
    },
    "bershka": {
        "budget": "budget",
        "aesthetic": {"en": "trend-driven streetwear", "ro": "streetwear orientat spre trend"},
        "style_tags": {"streetwear", "casual"},
        "strengths": {"hoodie", "jeans", "tshirt"},
    },
    "pull-and-bear": {
        "budget": "budget",
        "aesthetic": {"en": "young casual streetwear", "ro": "streetwear casual tineresc"},
        "style_tags": {"streetwear", "casual", "minimal"},
        "strengths": {"hoodie", "jeans", "pants"},
    },
    "massimo-dutti": {
        "budget": "premium",
        "aesthetic": {"en": "refined elevated basics", "ro": "basic-uri elevate și rafinate"},
        "style_tags": {"smart-casual", "minimal"},
        "strengths": {"pants", "tshirt", "jeans"},
    },
    "uniqlo": {
        "budget": "mid",
        "aesthetic": {"en": "quiet modern essentials", "ro": "esențiale moderne discrete"},
        "style_tags": {"minimal", "casual", "smart-casual"},
        "strengths": {"tshirt", "pants", "hoodie"},
    },
}

BUDGET_RANK = {"budget": 0, "mid": 1, "premium": 2}

CATEGORY_REASON_MAP = {
    "tshirt": {
        "en": "A clean tee gives the outfit a reliable base and is easy to style across brands.",
        "ro": "Un tricou curat îți oferă o bază sigură și e foarte ușor de combinat între branduri.",
    },
    "hoodie": {
        "en": "A hoodie works well when the request leans relaxed, sporty, or oversized.",
        "ro": "Un hanorac merge foarte bine când direcția este lejeră, sport sau oversized.",
    },
    "pants": {
        "en": "Structured pants help anchor smart-casual and minimal outfits.",
        "ro": "Pantalonii mai structurați ancorează bine ținutele smart-casual și minimal.",
    },
    "jeans": {
        "en": "Jeans are a safe everyday option when you want something versatile and easy to search for.",
        "ro": "Blugii rămân o opțiune sigură de zi cu zi când vrei ceva versatil și ușor de găsit.",
    },
}

STYLE_CATEGORY_ORDER = {
    "streetwear": ["hoodie", "jeans", "tshirt", "pants"],
    "minimal": ["tshirt", "pants", "jeans", "hoodie"],
    "smart-casual": ["pants", "tshirt", "jeans"],
    "athleisure": ["hoodie", "pants", "tshirt"],
    "casual": ["tshirt", "jeans", "hoodie", "pants"],
}

STYLE_LABELS = {
    "streetwear": {"en": "streetwear", "ro": "streetwear"},
    "minimal": {"en": "minimal", "ro": "minimal"},
    "smart-casual": {"en": "smart-casual", "ro": "smart-casual"},
    "athleisure": {"en": "athleisure", "ro": "athleisure"},
    "casual": {"en": "casual", "ro": "casual"},
}

CATEGORY_LABELS = {
    "tshirt": {"en": "t-shirt", "ro": "tricou"},
    "hoodie": {"en": "hoodie", "ro": "hanorac"},
    "pants": {"en": "pants", "ro": "pantaloni"},
    "jeans": {"en": "jeans", "ro": "blugi"},
}

COLOR_LABELS = {
    "black": {"en": "black", "ro": "negru"},
    "white": {"en": "white", "ro": "alb"},
    "grey": {"en": "grey", "ro": "gri"},
    "navy": {"en": "navy", "ro": "bleumarin"},
    "blue": {"en": "blue", "ro": "albastru"},
    "beige": {"en": "beige", "ro": "bej"},
    "brown": {"en": "brown", "ro": "maro"},
    "green": {"en": "green", "ro": "verde"},
    "olive": {"en": "olive", "ro": "olive"},
    "cream": {"en": "cream", "ro": "crem"},
    "pink": {"en": "pink", "ro": "roz"},
    "red": {"en": "red", "ro": "roșu"},
    "burgundy": {"en": "burgundy", "ro": "bordo"},
}

FIT_LABELS = {
    "oversized": {"en": "oversized", "ro": "oversized"},
    "regular": {"en": "regular", "ro": "regular"},
    "slim": {"en": "slim", "ro": "slim"},
    "skinny": {"en": "skinny", "ro": "skinny"},
    "relaxed": {"en": "relaxed", "ro": "relaxat"},
    "straight": {"en": "straight", "ro": "drept"},
    "wide": {"en": "wide-leg", "ro": "croi larg"},
    "baggy": {"en": "baggy", "ro": "baggy"},
}

SEASON_LABELS = {
    "spring": {"en": "spring", "ro": "primăvară"},
    "summer": {"en": "summer", "ro": "vară"},
    "autumn": {"en": "autumn", "ro": "toamnă"},
    "winter": {"en": "winter", "ro": "iarnă"},
}

OCCASION_LABELS = {
    "office": {"en": "office", "ro": "office"},
    "evening": {"en": "evening", "ro": "seară"},
    "event": {"en": "event", "ro": "eveniment"},
    "travel": {"en": "travel", "ro": "călătorie"},
    "weekend": {"en": "weekend", "ro": "weekend"},
    "gym": {"en": "gym", "ro": "sală"},
}


def build_stylist_recommendation(
    db: Session,
    user: User,
    payload: StylistRecommendationRequest,
) -> StylistRecommendationResponse:
    language = _detect_language(payload)
    season = _detect_season(payload)
    occasion = _detect_occasion(payload)
    brands = db.query(Brand).filter(Brand.active.is_(True)).all()
    categories = db.query(Category).all()
    category_by_code = {category.code: category for category in categories}

    detected_styles = _enrich_styles_for_context(_detect_style_tags(payload), occasion, season)
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
    selected_brands = ranked_brands[:4]

    category_codes = _pick_categories(payload.prompt, detected_styles, category_by_code)
    selected_categories = [category_by_code[code] for code in category_codes if code in category_by_code]
    fit_terms = _detect_fit_terms(payload.prompt, category_codes, user)

    search_queries = _build_search_queries(
        selected_brands,
        category_codes,
        colors,
        user,
        fit_terms,
        detected_styles,
        occasion,
        season,
        language,
    )
    outfit_ideas = _build_outfit_ideas(
        selected_brands,
        category_codes,
        colors,
        detected_styles,
        fit_terms,
        occasion,
        season,
        language,
    )
    fit_notes, warnings = _build_fit_notes(
        user,
        category_codes,
        colors,
        detected_styles,
        fit_terms,
        occasion,
        season,
        language,
    )

    size_suggestions: List[StylistSizeSuggestion] = []
    if payload.include_size_suggestions:
        size_suggestions = _build_size_suggestions(db, user, selected_brands, selected_categories, warnings, language)

    summary = _build_summary(
        brands=selected_brands,
        category_codes=category_codes,
        detected_styles=detected_styles,
        colors=colors,
        has_sizes=bool(size_suggestions),
        fit_terms=fit_terms,
        occasion=occasion,
        season=season,
        language=language,
    )

    return StylistRecommendationResponse(
        summary=summary,
        detected_style_tags=[_style_label(style, language) for style in detected_styles],
        recommended_colors=[_color_label(color, language) for color in colors],
        search_queries=search_queries,
        suggested_categories=[
            StylistCategorySuggestion(
                category_code=category.code,
                label=_category_label(category.code, language, fallback=category.name),
                reason=_category_reason(category.code, language),
            )
            for category in selected_categories
        ],
        suggested_brands=[
            StylistBrandSuggestion(
                brand_id=brand.id,
                brand_name=brand.name,
                reason=_brand_reason(brand.slug, detected_styles, language),
                aesthetic=_brand_aesthetic(brand.slug, language),
            )
            for brand in selected_brands[:3]
        ],
        size_suggestions=size_suggestions,
        outfit_ideas=outfit_ideas,
        fit_notes=fit_notes,
        warnings=warnings,
    )


def _detect_language(payload: StylistRecommendationRequest) -> str:
    haystack = _context_text(payload)
    if any(char in haystack for char in ROMANIAN_DIACRITICS):
        return "ro"

    normalized = _normalize_phrase(haystack)
    score = sum(1 for word in ROMANIAN_HINT_WORDS if _phrase_in_text(normalized, _normalize_phrase(word)))
    return "ro" if score >= 1 else "en"


def _detect_style_tags(payload: StylistRecommendationRequest) -> List[str]:
    normalized = _normalize_phrase(_context_text(payload))

    detected = [style for style, keywords in STYLE_KEYWORDS.items() if any(_phrase_in_text(normalized, _normalize_phrase(keyword)) for keyword in keywords)]
    if not detected:
        return DEFAULT_STYLE_ORDER.copy()
    if "casual" not in detected:
        detected.append("casual")
    return detected[:4]


def _context_text(payload: StylistRecommendationRequest) -> str:
    return " ".join(
        value
        for value in [
            payload.prompt,
            payload.occasion or "",
            payload.season or "",
            " ".join(payload.preferred_colors),
            " ".join(payload.preferred_brands),
        ]
        if value
    )


def _detect_season(payload: StylistRecommendationRequest) -> str | None:
    normalized = _normalize_phrase(_context_text(payload))
    for season in ("spring", "summer", "autumn", "winter"):
        if any(_phrase_in_text(normalized, _normalize_phrase(keyword)) for keyword in SEASON_KEYWORDS[season]):
            return season
    return None


def _detect_occasion(payload: StylistRecommendationRequest) -> str | None:
    normalized = _normalize_phrase(_context_text(payload))
    for occasion in ("office", "evening", "event", "travel", "weekend", "gym"):
        if any(_phrase_in_text(normalized, _normalize_phrase(keyword)) for keyword in OCCASION_KEYWORDS[occasion]):
            return occasion
    return None


def _enrich_styles_for_context(detected_styles: Sequence[str], occasion: str | None, season: str | None) -> List[str]:
    enriched = list(detected_styles) if detected_styles else DEFAULT_STYLE_ORDER.copy()

    for style in OCCASION_STYLE_HINTS.get(occasion or "", []):
        if style not in enriched:
            enriched.insert(0, style)

    for style in SEASON_STYLE_HINTS.get(season or "", []):
        if style not in enriched:
            enriched.append(style)

    if "casual" not in enriched:
        enriched.append("casual")
    return _ordered_unique(enriched)[:4]


def _detect_colors(payload: StylistRecommendationRequest) -> List[str]:
    colors: List[str] = []
    for color in payload.preferred_colors:
        canonical = _canonical_color(color)
        if canonical and canonical not in colors:
            colors.append(canonical)

    haystack = _normalize_phrase(payload.prompt)
    for color in COLOR_KEYWORDS:
        normalized_color = _normalize_phrase(color)
        if _phrase_in_text(haystack, normalized_color):
            canonical = _canonical_color(color)
            if canonical and canonical not in colors:
                colors.append(canonical)

    if not colors:
        colors = ["black", "white", "navy"]
    return colors[:4]


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
    prompt_lower = _normalize_phrase(prompt)
    explicit_matches: List[str] = []

    for code, aliases in CATEGORY_ALIASES.items():
        if code not in category_by_code:
            continue
        if any(_phrase_in_text(prompt_lower, _normalize_phrase(alias)) for alias in aliases):
            explicit_matches.append(code)

    if explicit_matches:
        return _ordered_unique(explicit_matches)[:3]

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
    detected_styles: Sequence[str],
    occasion: str | None,
    season: str | None,
    language: str,
) -> List[str]:
    queries: List[str] = []
    if not category_codes:
        return queries

    fallback_brand = brands[0].name if brands else ("brand preferat" if language == "ro" else "your preferred brand")
    fallback_color = colors[0] if colors else "black"
    lead_style = _style_label(detected_styles[0], language) if detected_styles else ("casual" if language == "en" else "casual")
    occasion_label = _occasion_label(occasion, language) if occasion else ""
    season_label = _season_label(season, language) if season else ""

    for index, category_code in enumerate(category_codes):
        brand_name = brands[index].name if index < len(brands) else fallback_brand
        color = colors[min(index, len(colors) - 1)] if colors else fallback_color
        fit_word = _search_fit_for_category(category_code, fit_terms, user, language)
        category_label = _category_label(category_code, language, fallback=category_code)
        color_label = _color_label(color, language)
        queries.append(f"{brand_name} {fit_word} {category_label} {color_label}".strip())
        queries.append(f"{brand_name} {lead_style} {category_label} {color_label}".strip())

        if occasion_label:
            queries.append(f"{brand_name} {occasion_label} {category_label} {color_label}".strip())
        if season_label:
            queries.append(f"{brand_name} {season_label} {fit_word} {category_label}".strip())

    while len(queries) < 5:
        category_code = category_codes[len(queries) % len(category_codes)]
        fit_word = _search_fit_for_category(category_code, fit_terms, user, language)
        category_label = _category_label(category_code, language, fallback=category_code)
        color_label = _color_label(fallback_color, language)
        query = f"{fallback_brand} {lead_style} {fit_word} {category_label} {color_label}".strip()
        if query not in queries:
            queries.append(query)
            continue
        break

    return _ordered_unique(queries)[:5]


def _build_outfit_ideas(
    brands: Sequence[Brand],
    category_codes: Sequence[str],
    colors: Sequence[str],
    detected_styles: Sequence[str],
    fit_terms: Dict[str, List[str]],
    occasion: str | None,
    season: str | None,
    language: str,
) -> List[StylistOutfitIdea]:
    primary_brand = brands[0].name if brands else "TryClothes"
    accent_brand = brands[1].name if len(brands) > 1 else primary_brand
    support_brand = brands[2].name if len(brands) > 2 else accent_brand
    primary_color = colors[0] if colors else "black"
    secondary_color = colors[1] if len(colors) > 1 else "white"
    tertiary_color = colors[2] if len(colors) > 2 else secondary_color
    lead_style = detected_styles[0] if detected_styles else "casual"

    top_category = next((code for code in category_codes if code in TOP_CATEGORY_CODES), "tshirt")
    bottom_category = next((code for code in category_codes if code in BOTTOM_CATEGORY_CODES), "jeans")
    top_shape = _outfit_fit_phrase(top_category, fit_terms, language)
    bottom_shape = _outfit_fit_phrase(bottom_category, fit_terms, language)
    occasion_piece = _occasion_support_piece(occasion, language)
    season_piece = _season_support_piece(season, language)
    context_title = _context_idea_title(occasion, season, language)
    context_summary = _context_idea_summary(lead_style, occasion, season, language)

    if language == "ro":
        ideas = [
            StylistOutfitIdea(
                title="Direcția principală",
                summary="Pleacă de la o bază clară și lasă o singură piesă să conducă volumul, ca outfitul să pară curat și intenționat.",
                pieces=[
                    f"{primary_brand} {_color_label(primary_color, language)} {top_shape} {_category_label(top_category, language, top_category)}",
                    f"{accent_brand} {_color_label(secondary_color, language)} {bottom_shape} {_category_label(bottom_category, language, bottom_category)}",
                    occasion_piece,
                ],
            ),
            StylistOutfitIdea(
                title="Variantă sigură și premium",
                summary="Direcție curată, ușor de repetat, cu o paletă neutră și proporții care rămân actuale mai mult timp.",
                pieces=[
                    f"{primary_brand} {_color_label(primary_color, language)} tricou regular",
                    f"{accent_brand} {_color_label(secondary_color, language)} blugi drepți",
                    season_piece,
                ],
            ),
            StylistOutfitIdea(
                title="Accent controlat",
                summary="Aici lași o piesă să iasă mai mult în față, iar restul doar susțin linia generală fără să încarce.",
                pieces=[
                    f"{primary_brand} {top_shape} {_category_label(top_category, language, top_category)} {_color_label(primary_color, language)}",
                    f"{support_brand} {bottom_shape} {_category_label(bottom_category, language, bottom_category)} {_color_label(tertiary_color, language)}",
                    "Accesorii discrete și încălțăminte simplă",
                ],
            ),
            StylistOutfitIdea(
                title=context_title,
                summary=context_summary,
                pieces=[
                    f"{accent_brand} {_color_label(primary_color, language)} {_category_label(top_category, language, top_category)}",
                    f"{support_brand} {_color_label(secondary_color, language)} {_category_label(bottom_category, language, bottom_category)}",
                    season_piece,
                    occasion_piece,
                ],
            ),
        ]
    else:
        ideas = [
            StylistOutfitIdea(
                title="Core direction",
                summary="Start from a clean base and let only one piece carry the volume so the outfit feels clear and intentional.",
                pieces=[
                    f"{primary_brand} {_color_label(primary_color, language)} {top_shape} {_category_label(top_category, language, top_category)}",
                    f"{accent_brand} {_color_label(secondary_color, language)} {bottom_shape} {_category_label(bottom_category, language, bottom_category)}",
                    occasion_piece,
                ],
            ),
            StylistOutfitIdea(
                title="Safer polished option",
                summary="A cleaner fallback built around neutrals and proportions that stay wearable across more situations.",
                pieces=[
                    f"{primary_brand} {_color_label(primary_color, language)} regular t-shirt",
                    f"{accent_brand} {_color_label(secondary_color, language)} straight jeans",
                    season_piece,
                ],
            ),
            StylistOutfitIdea(
                title="Controlled statement look",
                summary="Let one piece lead visually and keep the rest calmer so the outfit still reads refined instead of busy.",
                pieces=[
                    f"{primary_brand} {top_shape} {_category_label(top_category, language, top_category)} in {_color_label(primary_color, language)}",
                    f"{support_brand} {bottom_shape} {_category_label(bottom_category, language, bottom_category)} in {_color_label(tertiary_color, language)}",
                    "Simple footwear and low-noise accessories",
                ],
            ),
            StylistOutfitIdea(
                title=context_title,
                summary=context_summary,
                pieces=[
                    f"{accent_brand} {_color_label(primary_color, language)} {_category_label(top_category, language, top_category)}",
                    f"{support_brand} {_color_label(secondary_color, language)} {_category_label(bottom_category, language, bottom_category)}",
                    season_piece,
                    occasion_piece,
                ],
            ),
        ]
    return _ordered_outfit_ideas(ideas)


def _ordered_outfit_ideas(ideas: Sequence[StylistOutfitIdea]) -> List[StylistOutfitIdea]:
    ordered: List[StylistOutfitIdea] = []
    for idea in ideas:
        if idea.title not in {existing.title for existing in ordered}:
            ordered.append(idea)
    return ordered[:4]


def _context_idea_title(occasion: str | None, season: str | None, language: str) -> str:
    if occasion == "office":
        return "Office rafinat" if language == "ro" else "Refined office option"
    if occasion == "evening":
        return "Seară simplă dar puternică" if language == "ro" else "Clean evening option"
    if occasion == "event":
        return "Variantă pentru eveniment" if language == "ro" else "Event-ready option"
    if occasion == "travel":
        return "Travel light" if language == "ro" else "Travel light"
    if occasion == "gym":
        return "Sport curat" if language == "ro" else "Clean training direction"
    if season == "winter":
        return "Layering de iarnă" if language == "ro" else "Winter layering direction"
    if season == "summer":
        return "Variantă fresh" if language == "ro" else "Fresh summer option"
    return "A doua direcție utilă" if language == "ro" else "Extra useful direction"


def _context_idea_summary(lead_style: str, occasion: str | None, season: str | None, language: str) -> str:
    style_label = _style_label(lead_style, language)

    if language == "ro":
        if occasion == "office":
            return f"Păstrează vibe-ul {style_label}, dar mută accentul spre linii mai curate, pantofi simpli și un layer structurat."
        if occasion == "evening":
            return f"Pentru seară, lasă contrastul și textura să facă treaba, nu foarte multe piese sau culori simultan."
        if occasion == "event":
            return f"Pentru eveniment, mergi pe proporții curate și pe o piesă care arată premium fără să pară rigidă."
        if occasion == "travel":
            return f"Pentru călătorie, confortul trebuie să rămână primul, dar cu o siluetă destul de curată încât să nu pară dezordonată."
        if occasion == "gym":
            return f"Pentru sală sau drum spre sală, păstrează piesele mobile, respirabile și simple ca linie."
        if season == "winter":
            return "Iarna arată cel mai bine când stratul exterior aduce structură și restul pieselor rămân coerente în culoare."
        if season == "summer":
            return "Vara funcționează mai bine cu materiale mai ușoare, mai puține straturi și o paletă care respiră."
        return f"Această variantă păstrează direcția {style_label}, dar îți dă o opțiune mai ușor de purtat repetat."

    if occasion == "office":
        return f"Keep the {style_label} attitude, but push the outfit toward cleaner lines, simpler shoes, and a more structured top layer."
    if occasion == "evening":
        return "For evening wear, let contrast and texture do the work instead of stacking too many loud pieces."
    if occasion == "event":
        return "For an event, cleaner proportions and one premium-looking piece usually land better than too much styling noise."
    if occasion == "travel":
        return "For travel, comfort should stay first, but the silhouette still needs enough structure to feel intentional."
    if occasion == "gym":
        return "For training or the trip to the gym, keep the pieces mobile, breathable, and simple in line."
    if season == "winter":
        return "Winter looks better when the outer layer adds structure and the rest of the palette stays consistent."
    if season == "summer":
        return "Summer looks cleaner with lighter fabrics, fewer layers, and a palette that breathes."
    return f"This variation keeps the {style_label} direction but makes it easier to repeat in everyday wear."


def _occasion_support_piece(occasion: str | None, language: str) -> str:
    if occasion == "office":
        return "Pantofi curați și layer structurat" if language == "ro" else "Clean shoes and a structured layer"
    if occasion == "evening":
        return "Pantofi low-profile și accesorii discrete" if language == "ro" else "Low-profile shoes and quiet accessories"
    if occasion == "event":
        return "Un layer premium fără detalii prea multe" if language == "ro" else "A premium layer with low visual noise"
    if occasion == "travel":
        return "Sneakers comozi și geantă simplă" if language == "ro" else "Comfortable sneakers and a simple bag"
    if occasion == "gym":
        return "Sneakers sport și layer ușor" if language == "ro" else "Training shoes and a light outer layer"
    return "Sneakers curați sau pantofi low-profile" if language == "ro" else "Clean sneakers or low-profile shoes"


def _season_support_piece(season: str | None, language: str) -> str:
    if season == "winter":
        return "Palton scurt, bomber sau layer gros neutru" if language == "ro" else "A short coat, bomber, or heavier neutral layer"
    if season == "autumn":
        return "Overshirt, bomber sau jachetă texturată" if language == "ro" else "An overshirt, bomber, or textured jacket"
    if season == "summer":
        return "Layer foarte ușor sau outfit purtat fără strat suplimentar" if language == "ro" else "A very light layer or no extra top layer at all"
    if season == "spring":
        return "Jachetă subțire sau overshirt curat" if language == "ro" else "A light jacket or clean overshirt"
    return "Layer subțire sau jachetă neutră" if language == "ro" else "A light neutral layer or jacket"


def _build_fit_notes(
    user: User,
    category_codes: Sequence[str],
    colors: Sequence[str],
    detected_styles: Sequence[str],
    fit_terms: Dict[str, List[str]],
    occasion: str | None,
    season: str | None,
    language: str,
) -> tuple[List[str], List[str]]:
    profile = user.body_profile
    fit_notes: List[str] = []
    warnings: List[str] = []

    if not profile:
        fit_notes.append(
            "Adaugă măsurătorile tale ca să primești recomandări mai precise între branduri."
            if language == "ro"
            else "Add your measurements to unlock more accurate guidance across brands."
        )
        warnings.append(
            "Profilul corporal lipsește, deci recomandările de fit sunt bazate mai mult pe stil decât pe măsurători exacte."
            if language == "ro"
            else "Your body profile is missing, so fit advice is based more on styling logic than exact measurements."
        )
    else:
        fit_notes.append(
            f"Preferința ta salvată este {_fit_label(profile.fit_preference.lower(), language)}."
            if language == "ro"
            else f"Your saved fit preference is {_fit_label(profile.fit_preference.lower(), language)}."
        )
        missing = []
        for label, attr in (
            ("chest", profile.chest_cm),
            ("waist", profile.waist_cm),
            ("hips", profile.hips_cm),
        ):
            if attr is None:
                missing.append(label)

        if missing:
            warnings.append(
                f"Mai adaugă {', '.join(missing)} pentru un sizing mai sigur pe categorii."
                if language == "ro"
                else f"Add {', '.join(missing)} for stronger category-specific sizing accuracy."
            )
        else:
            fit_notes.append(
                "Ai bust, talie și șolduri salvate, deci predicțiile de mărime ar trebui să fie mai stabile."
                if language == "ro"
                else "Chest, waist, and hips are saved, so cross-brand size predictions should be more stable."
            )

    silhouette_notes, silhouette_warnings = _build_silhouette_notes(fit_terms, category_codes, detected_styles, user, language)
    color_notes, color_warnings = _build_color_notes(colors, language)
    context_notes, context_warnings = _build_context_notes(category_codes, colors, occasion, season, language)

    fit_notes.extend(silhouette_notes)
    fit_notes.extend(color_notes)
    fit_notes.extend(context_notes)
    warnings.extend(silhouette_warnings)
    warnings.extend(color_warnings)
    warnings.extend(context_warnings)

    return _ordered_unique(fit_notes), _ordered_unique(warnings)


def _build_context_notes(
    category_codes: Sequence[str],
    colors: Sequence[str],
    occasion: str | None,
    season: str | None,
    language: str,
) -> tuple[List[str], List[str]]:
    notes: List[str] = []
    warnings: List[str] = []
    normalized_colors = [color.lower() for color in colors[:2]]

    if occasion == "office":
        notes.append(
            "Pentru office, liniile curate și încălțămintea simplă vor face look-ul să pară mai scump și mai sigur."
            if language == "ro"
            else "For office wear, cleaner lines and simpler footwear usually make the outfit feel more expensive and more reliable."
        )
    elif occasion == "evening":
        notes.append(
            "Pentru seară, contrastul și textura sunt mai utile decât prea multe culori sau multe straturi."
            if language == "ro"
            else "For evening wear, contrast and texture help more than stacking too many colors or layers."
        )
    elif occasion == "travel":
        notes.append(
            "Pentru călătorie, o talie comodă și un top mai relaxat îți vor salva cel mai mult outfitul."
            if language == "ro"
            else "For travel, a comfortable waist and a more relaxed top will usually save the outfit."
        )
    elif occasion == "gym":
        notes.append(
            "Pentru sală, prioritizează mobilitatea și materialele ușoare, chiar dacă look-ul rămâne premium."
            if language == "ro"
            else "For the gym, prioritize movement and lighter fabrics even if you still want the look to stay premium."
        )

    if season == "summer":
        notes.append(
            "Vara, culorile deschise și straturile puține păstrează outfitul mai fresh."
            if language == "ro"
            else "In summer, lighter colors and fewer layers usually keep the outfit fresher."
        )
        if normalized_colors and all(color in {"black", "navy", "burgundy", "brown"} for color in normalized_colors):
            warnings.append(
                "Dacă păstrezi doar culori închise vara, ajută mult o textură mai ușoară sau un pantof mai luminos."
                if language == "ro"
                else "If you keep only darker colors in summer, a lighter fabric or brighter shoe helps a lot."
            )
    elif season == "winter":
        notes.append(
            "Iarna, un strat exterior cu structură face ca și cele mai simple piese să arate mai bine."
            if language == "ro"
            else "In winter, a structured outer layer makes even the simplest base pieces look better."
        )

    if any(code in BOTTOM_CATEGORY_CODES for code in category_codes) and occasion == "office":
        warnings.append(
            "Dacă alegi jeans pentru office, merg mai bine într-o spălare curată și fără rupturi vizibile."
            if language == "ro"
            else "If you choose jeans for office wear, cleaner washes with no visible distressing usually work better."
        )

    return notes, warnings


def _build_silhouette_notes(
    fit_terms: Dict[str, List[str]],
    category_codes: Sequence[str],
    detected_styles: Sequence[str],
    user: User,
    language: str,
) -> tuple[List[str], List[str]]:
    top_terms = set(fit_terms.get("top", []))
    bottom_terms = set(fit_terms.get("bottom", []))
    has_top = any(code in TOP_CATEGORY_CODES for code in category_codes)
    has_bottom = any(code in BOTTOM_CATEGORY_CODES for code in category_codes)
    lead_style = detected_styles[0] if detected_styles else "casual"

    notes: List[str] = []
    warnings: List[str] = []

    if "oversized" in top_terms and bottom_terms.intersection({"skinny", "slim"}):
        notes.append(
            "Dacă păstrezi topul oversized, niște blugi drepți sau relaxați vor echilibra mai bine silueta."
            if language == "ro"
            else "If you keep the top oversized, straight or relaxed denim will usually balance the silhouette better."
        )
        warnings.append(
            "Top oversized plus pantaloni skinny poate face ținuta să pară disproporționată și puțin învechită."
            if language == "ro"
            else "An oversized top with skinny bottoms can make the outfit feel top-heavy and slightly dated."
        )
    elif "oversized" in top_terms and bottom_terms.intersection({"wide", "baggy", "relaxed"}):
        notes.append(
            "Oversized sus și larg jos poate funcționa dacă lași o piesă să aibă mai multă structură."
            if language == "ro"
            else "Oversized on top with wide or baggy bottoms can work when one piece still keeps some structure."
        )
        warnings.append(
            "Dacă ambele piese sunt prea largi, ținuta poate pierde formă. Ajută un tiv mai scurt, un tuck sau pantofi mai curați."
            if language == "ro"
            else "If both pieces are very loose, the outfit can lose shape. A shorter hem, a tuck, or cleaner shoes help a lot."
        )
    elif top_terms.intersection({"slim", "regular"}) and bottom_terms.intersection({"wide", "relaxed", "baggy"}):
        notes.append(
            "Un top curat cu pantaloni mai largi este una dintre cele mai ușoare formule pentru o siluetă echilibrată."
            if language == "ro"
            else "A cleaner top with wider pants is one of the easiest formulas for a balanced silhouette."
        )
    elif top_terms.intersection({"slim"}) and bottom_terms.intersection({"skinny", "slim"}):
        notes.append(
            "Slim sus și slim jos arată precis, dar creează o linie foarte strânsă din cap până în picioare."
            if language == "ro"
            else "Slim on top and slim on the bottom looks sharp, but it creates a very fitted head-to-toe line."
        )
        warnings.append(
            "Dacă vrei ceva mai actual, schimbă partea de jos spre straight sau relaxed."
            if language == "ro"
            else "If you want something more current, move the bottom half toward straight or relaxed."
        )
    elif top_terms.intersection({"regular"}) and bottom_terms.intersection({"regular", "straight"}):
        notes.append(
            "Regular sus cu straight jos este cea mai sigură combinație pentru o ținută versatilă."
            if language == "ro"
            else "Regular on top with straight bottoms is the safest versatile combination."
        )

    if has_top and "oversized" in top_terms and not bottom_terms:
        notes.append(
            "Un top oversized merge cel mai bine cu partea de jos mai dreaptă sau ușor relaxată."
            if language == "ro"
            else "An oversized top usually works best with straighter or lightly relaxed bottoms."
        )
    if has_bottom and "skinny" in bottom_terms and not top_terms:
        notes.append(
            "Pantalonii skinny arată mai bine cu un top regular sau slim decât cu unul foarte voluminos."
            if language == "ro"
            else "Skinny bottoms usually look better with a regular or slim top than with a very oversized one."
        )
    if has_bottom and bottom_terms.intersection({"wide", "relaxed", "baggy"}) and not top_terms:
        notes.append(
            "Pantalonii largi arată mai curat când topul rămâne regular, slim sau doar ușor scurtat."
            if language == "ro"
            else "Wide or relaxed pants look cleaner when the top stays regular, slim, or slightly cropped."
        )

    if not notes:
        profile_fit = (user.body_profile.fit_preference.lower() if user.body_profile and user.body_profile.fit_preference else "regular")
        if profile_fit == "oversized":
            notes.append(
                "Cum preferința ta e oversized, păstrează cealaltă jumătate mai curată ca să nu se adune prea mult volum."
                if language == "ro"
                else "Because your preference leans oversized, keep the other half cleaner so the volume does not stack everywhere."
            )
        elif profile_fit == "slim":
            notes.append(
                "Cum preferința ta e slim, partea de jos straight sau relaxed va păstra ținuta mai actuală."
                if language == "ro"
                else "Because your preference leans slim, straight or relaxed bottoms usually keep the outfit more current."
            )
        elif profile_fit == "regular":
            notes.append(
                "Regular este cel mai bun punct de pornire, apoi poți împinge volumul doar într-o singură piesă."
                if language == "ro"
                else "Regular is the safest starting point, then you can push more volume into just one piece."
            )
        elif lead_style == "streetwear":
            notes.append(
                "Streetwear arată cel mai bine când o singură piesă duce volumul și restul rămâne mai curat."
                if language == "ro"
                else "Streetwear usually looks best when one piece carries the volume and the rest stays cleaner."
            )
        else:
            notes.append(
                "Păstrează volumul pe o singură jumătate a ținutei și tot look-ul va părea mai clar."
                if language == "ro"
                else "Keep the volume on only one half of the outfit and the overall look will read more clearly."
            )

    return notes, warnings


def _build_color_notes(colors: Sequence[str], language: str) -> tuple[List[str], List[str]]:
    normalized_colors = [color.lower() for color in colors if color]
    primary_colors = normalized_colors[:2]
    if not primary_colors:
        return [], []

    notes: List[str] = []
    warnings: List[str] = []
    pair_key = frozenset(primary_colors)

    if pair_key in COLOR_COMBINATION_NOTES:
        notes.append(COLOR_COMBINATION_NOTES[pair_key][language])
    elif all(color in NEUTRAL_COLORS for color in primary_colors):
        notes.append(
            "Culorile principale sunt neutre, deci ținuta va fi mai curată și mult mai ușor de repetat."
            if language == "ro"
            else "Your lead colors are neutral, so the outfit should feel cleaner and much easier to repeat."
        )
    elif any(color in NEUTRAL_COLORS for color in primary_colors):
        notes.append(
            "O neutră plus o culoare accent păstrează look-ul purtabil fără să devină plictisitor."
            if language == "ro"
            else "One neutral plus one accent color keeps the look wearable without making it flat."
        )
    elif _color_family(primary_colors[0]) == _color_family(primary_colors[1]):
        notes.append(
            "Primele două culori sunt din aceeași familie, deci paleta va părea coerentă."
            if language == "ro"
            else "The first two colors sit in a similar family, so the palette should feel cohesive."
        )

    if len(primary_colors) == 2 and not any(color in NEUTRAL_COLORS for color in primary_colors):
        if _color_family(primary_colors[0]) != _color_family(primary_colors[1]):
            warnings.append(
                "Dacă amesteci două culori puternice, adaugă un pantof sau un layer neutru ca ținuta să nu devină încărcată."
                if language == "ro"
                else "If you mix two stronger colors, add a neutral shoe or layer so the outfit does not become too busy."
            )

    return notes, warnings


def _build_size_suggestions(
    db: Session,
    user: User,
    brands: Sequence[Brand],
    categories: Sequence[Category],
    warnings: List[str],
    language: str,
) -> List[StylistSizeSuggestion]:
    suggestions: List[StylistSizeSuggestion] = []
    if not user.body_profile:
        return suggestions

    for brand in brands[:3]:
        for category in categories[:2]:
            size_label, confidence_score, reasons, _chart = suggest_best_size(db, user, brand, category)
            if not size_label:
                continue
            localized_reasons = [_localize_size_reason(reason, language) for reason in reasons[:4]]
            suggestions.append(
                StylistSizeSuggestion(
                    brand_id=brand.id,
                    brand_name=brand.name,
                    category_code=category.code,
                    size_label=size_label,
                    confidence_score=confidence_score,
                    reasons=localized_reasons,
                )
            )

    if not suggestions and user.body_profile:
        warnings.append(
            "Nu am putut genera mărimi recomandate fiindcă lipsesc size chart-uri compatibile pentru brandurile selectate."
            if language == "ro"
            else "Size suggestions could not be generated because compatible size charts are missing for the selected brands."
        )
    return suggestions[:6]


def _build_summary(
    *,
    brands: Sequence[Brand],
    category_codes: Sequence[str],
    detected_styles: Sequence[str],
    colors: Sequence[str],
    has_sizes: bool,
    fit_terms: Dict[str, List[str]],
    occasion: str | None,
    season: str | None,
    language: str,
) -> str:
    brand_names = ", ".join(brand.name for brand in brands[:2]) or ("branduri disponibile" if language == "ro" else "available brands")
    category_summary = ", ".join(_category_label(code, language, code) for code in category_codes[:2]) or ("piese de bază" if language == "ro" else "basics")
    style_summary = ", ".join(_style_label(style, language) for style in detected_styles[:2])
    color_summary = ", ".join(_color_label(color, language) for color in colors[:2])
    silhouette_summary = _summary_fit_line(fit_terms, language)
    context_prefix = _summary_context_prefix(occasion, season, language)
    sizing_suffix = (
        " Am inclus și ghidaj de mărime."
        if has_sizes and language == "ro"
        else " Size guidance is included."
        if has_sizes
        else " Adaugă mai multe măsurători pentru recomandări de mărime mai bune."
        if language == "ro"
        else " Add more measurements to unlock stronger size guidance."
    )

    if language == "ro":
        return (
            f"{context_prefix}Ți-am pregătit mai multe direcții {style_summary} în jurul pieselor {category_summary}, "
            f"cu culori precum {color_summary} și cu {brand_names} ca puncte de plecare cele mai bune. "
            f"{silhouette_summary}{sizing_suffix}"
        )

    return (
        f"{context_prefix}I prepared multiple {style_summary} directions around {category_summary}, with colors like {color_summary} "
        f"and {brand_names} as the strongest starting points. {silhouette_summary}{sizing_suffix}"
    )


def _summary_fit_line(fit_terms: Dict[str, List[str]], language: str) -> str:
    top_terms = set(fit_terms.get("top", []))
    bottom_terms = set(fit_terms.get("bottom", []))

    if "oversized" in top_terms and bottom_terms.intersection({"skinny", "slim"}):
        return (
            "Schimbă partea de jos spre straight sau relaxed dacă vrei o siluetă mai echilibrată."
            if language == "ro"
            else "Move the bottom half toward straight or relaxed if you want a more balanced silhouette."
        )
    if "oversized" in top_terms and bottom_terms.intersection({"wide", "baggy", "relaxed"}):
        return (
            "Lasă o singură piesă largă să conducă ținuta și păstrează restul puțin mai curat."
            if language == "ro"
            else "Let one loose piece lead the outfit and keep the rest a little cleaner."
        )
    if top_terms.intersection({"slim", "regular"}) and bottom_terms.intersection({"wide", "relaxed", "baggy"}):
        return (
            "Silueta e deja echilibrată pentru că volumul stă mai mult pe partea de jos."
            if language == "ro"
            else "The silhouette already feels balanced because the volume sits more on the lower half."
        )
    if "oversized" in top_terms:
        return (
            "Lasă piesa oversized să iasă în față și păstrează restul ținutei mai curat."
            if language == "ro"
            else "Let the oversized piece lead and keep the rest of the outfit cleaner."
        )
    return (
        "Ține volumul doar pe o jumătate a outfitului și look-ul va părea mai clar."
        if language == "ro"
        else "Keep the volume on only one half of the outfit and the whole look will read more clearly."
    )


def _brand_reason(brand_slug: str, detected_styles: Sequence[str], language: str) -> str:
    metadata = BRAND_STYLE_MAP.get(brand_slug, {})
    strengths = sorted(metadata.get("strengths", {"basics"}))
    style_tags = sorted(metadata.get("style_tags", {"casual"}).intersection(detected_styles))
    strengths_text = ", ".join(_category_label(value, language, value) for value in strengths[:2])
    style_text = ", ".join(_style_label(value, language) for value in style_tags) if style_tags else ("ținute de zi cu zi" if language == "ro" else "everyday wear")

    if language == "ro":
        return f"Este puternic pe {strengths_text} și se potrivește bine cu cereri de tip {style_text}."
    return f"Strong for {strengths_text} and lines up well with {style_text} requests."


def _brand_aesthetic(brand_slug: str, language: str) -> str:
    metadata = BRAND_STYLE_MAP.get(brand_slug, {})
    aesthetic = metadata.get("aesthetic")
    if isinstance(aesthetic, dict):
        return str(aesthetic.get(language) or aesthetic.get("en") or "versatile everyday basics")
    return "versatile everyday basics" if language == "en" else "basic-uri versatile de zi cu zi"


def _category_reason(category_code: str, language: str) -> str:
    return CATEGORY_REASON_MAP.get(category_code, {}).get(
        language,
        "This category matches the brief."
        if language == "en"
        else "Categoria se potrivește cu direcția cerută.",
    )


def _search_fit_for_category(category_code: str, fit_terms: Dict[str, List[str]], user: User, language: str) -> str:
    top_terms = fit_terms.get("top", [])
    bottom_terms = fit_terms.get("bottom", [])
    general_terms = fit_terms.get("general", [])
    profile_fit = user.body_profile.fit_preference.lower() if user.body_profile and user.body_profile.fit_preference else "regular"

    if category_code in TOP_CATEGORY_CODES:
        value = (
            _first_relevant_term(top_terms, TOP_RELEVANT_FITS)
            or ("regular" if "skinny" in bottom_terms else None)
            or _first_relevant_term(general_terms, TOP_RELEVANT_FITS)
            or (profile_fit if profile_fit in TOP_RELEVANT_FITS else "regular")
        )
        return _fit_label(value, language)

    if category_code in BOTTOM_CATEGORY_CODES:
        value = (
            _first_relevant_term(bottom_terms, BOTTOM_RELEVANT_FITS)
            or ("straight" if "oversized" in top_terms else None)
            or ("straight" if "slim" in top_terms else None)
            or _first_relevant_term(general_terms, BOTTOM_RELEVANT_FITS)
            or (profile_fit if profile_fit in BOTTOM_RELEVANT_FITS else "regular")
        )
        return _fit_label(value, language)

    return _fit_label(_first_relevant_term(general_terms, FIT_TERM_ORDER) or profile_fit, language)


def _outfit_fit_phrase(category_code: str, fit_terms: Dict[str, List[str]], language: str) -> str:
    if category_code in TOP_CATEGORY_CODES:
        if "oversized" in fit_terms.get("top", []):
            return _fit_label("oversized", language)
        if "slim" in fit_terms.get("top", []):
            return "clean slim" if language == "en" else "slim curat"
        if "baggy" in fit_terms.get("top", []):
            return _fit_label("relaxed", language)
        return _fit_label("regular", language)

    if category_code in BOTTOM_CATEGORY_CODES:
        if "skinny" in fit_terms.get("bottom", []):
            return _fit_label("skinny", language)
        if "wide" in fit_terms.get("bottom", []):
            return _fit_label("wide", language)
        if "baggy" in fit_terms.get("bottom", []) or "relaxed" in fit_terms.get("bottom", []):
            return _fit_label("relaxed", language)
        if "straight" in fit_terms.get("bottom", []):
            return _fit_label("straight", language)
        if "oversized" in fit_terms.get("top", []):
            return _fit_label("straight", language)
        return _fit_label("straight", language)

    return "clean" if language == "en" else "curat"


def _localize_size_reason(reason: str, language: str) -> str:
    if language == "en":
        return reason

    replacements = {
        "Chest": "Bust",
        "Waist": "Talie",
        "Hips": "Șolduri",
        "Inseam": "Lungime interioară",
        "No body profile measurements saved.": "Nu există măsurători salvate în profilul corporal.",
        "No matching size chart entries found.": "Nu am găsit intrări compatibile în size chart.",
    }
    translated = reason
    for source, target in replacements.items():
        translated = translated.replace(source, target)
    return translated


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
    normalized = unicodedata.normalize("NFD", value.lower())
    without_diacritics = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return " ".join(without_diacritics.replace("-", " ").replace("/", " ").split())


def _canonical_color(value: str) -> str | None:
    return COLOR_ALIASES.get(_normalize_phrase(value))


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


def _style_label(style: str, language: str) -> str:
    return STYLE_LABELS.get(style, {}).get(language, style)


def _season_label(season: str, language: str) -> str:
    return SEASON_LABELS.get(season, {}).get(language, season)


def _occasion_label(occasion: str, language: str) -> str:
    return OCCASION_LABELS.get(occasion, {}).get(language, occasion)


def _summary_context_prefix(occasion: str | None, season: str | None, language: str) -> str:
    if occasion and season:
        if language == "ro":
            return f"Pentru {_occasion_label(occasion, language)} în {_season_label(season, language)}, "
        return f"For {_occasion_label(occasion, language)} wear in {_season_label(season, language)}, "

    if occasion:
        if language == "ro":
            return f"Pentru {_occasion_label(occasion, language)}, "
        return f"For {_occasion_label(occasion, language)} wear, "

    if season:
        if language == "ro":
            return f"Pentru {_season_label(season, language)}, "
        return f"For {_season_label(season, language)}, "

    return ""


def _category_label(category_code: str, language: str, fallback: str) -> str:
    return CATEGORY_LABELS.get(category_code, {}).get(language, fallback)


def _color_label(color: str, language: str) -> str:
    return COLOR_LABELS.get(color, {}).get(language, color)


def _fit_label(fit: str, language: str) -> str:
    return FIT_LABELS.get(fit, {}).get(language, fit)
