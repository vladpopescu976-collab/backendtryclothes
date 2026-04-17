from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.body_profile import BodyProfile
from app.models.brand import Brand
from app.models.brand_size_chart import BrandSizeChart
from app.models.brand_size_chart_entry import BrandSizeChartEntry
from app.models.category import Category
from app.models.fit_prediction import FitPrediction
from app.models.user import User

MEASUREMENT_PRIORITY = {
    "tshirt": ["chest", "waist"],
    "hoodie": ["chest", "waist"],
    "pants": ["waist", "hips", "inseam"],
    "jeans": ["waist", "hips", "inseam"],
}


def predict_fit(
    db: Session,
    user: User,
    brand: Brand,
    category: Category,
    size_label: str,
    garment_asset_id: Optional[str] = None,
) -> Tuple[FitPrediction, BrandSizeChart]:
    profile = user.body_profile
    if not profile:
        raise ValueError("User body profile is required before requesting fit prediction.")

    chart = _select_chart(db, brand.id, category.id, profile.sex_fit)
    if not chart:
        raise LookupError("No size chart available for this brand and category.")

    entry = _find_chart_entry(chart, size_label)
    if not entry:
        raise LookupError("Requested size label is not available in the selected size chart.")

    fit_result, confidence_score, reasons = _calculate_fit(profile, category.code, entry, chart.source_type)

    prediction = FitPrediction(
        user_id=user.id,
        garment_asset_id=garment_asset_id,
        brand_id=brand.id,
        category_id=category.id,
        size_label=size_label.upper(),
        fit_result=fit_result,
        confidence_score=confidence_score,
        reason_json=reasons,
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return prediction, chart


def suggest_best_size(
    db: Session,
    user: User,
    brand: Brand,
    category: Category,
) -> Tuple[Optional[str], float, List[str], Optional[BrandSizeChart]]:
    profile = user.body_profile
    if not profile:
        return None, 0.2, ["Body profile is missing, so a size estimate cannot be generated yet."], None

    chart = _select_chart(db, brand.id, category.id, profile.sex_fit)
    if not chart:
        return None, 0.25, ["This brand does not have a size chart for the selected category yet."], None

    best_entry: Optional[BrandSizeChartEntry] = None
    best_score: Optional[float] = None
    best_reasons: List[str] = []
    metrics_used = 0

    for entry in chart.entries:
        entry_score, entry_reasons, entry_metrics = _score_chart_entry(profile, category.code, entry)
        if entry_metrics == 0:
            continue
        if best_score is None or entry_score < best_score:
            best_entry = entry
            best_score = entry_score
            best_reasons = entry_reasons
            metrics_used = entry_metrics

    if not best_entry or best_score is None:
        return None, 0.3, ["Not enough measurements are available to map the user to a likely size."], chart

    confidence_score = 0.48 + min(metrics_used * 0.1, 0.28)
    if chart.source_type == "seed_estimate":
        confidence_score -= 0.08
        best_reasons.append("This size suggestion uses seeded brand-level sizing data and should be treated as directional.")

    return best_entry.size_label.upper(), round(max(0.2, min(confidence_score, 0.92)), 3), best_reasons, chart


def _select_chart(db: Session, brand_id: str, category_id: str, sex_fit: str) -> Optional[BrandSizeChart]:
    exact_chart = (
        db.query(BrandSizeChart)
        .filter(
            BrandSizeChart.brand_id == brand_id,
            BrandSizeChart.category_id == category_id,
            BrandSizeChart.gender_fit == sex_fit,
        )
        .first()
    )
    if exact_chart:
        return exact_chart

    return (
        db.query(BrandSizeChart)
        .filter(
            BrandSizeChart.brand_id == brand_id,
            BrandSizeChart.category_id == category_id,
            BrandSizeChart.gender_fit == "unisex",
        )
        .first()
    )


def _find_chart_entry(chart: BrandSizeChart, size_label: str) -> Optional[BrandSizeChartEntry]:
    normalized_size = size_label.upper().strip()
    for entry in chart.entries:
        if entry.size_label.upper() == normalized_size:
            return entry
    return None


def _calculate_fit(
    profile: BodyProfile,
    category_code: str,
    entry: BrandSizeChartEntry,
    source_type: str,
) -> Tuple[str, float, List[str]]:
    measurement_keys = MEASUREMENT_PRIORITY.get(category_code, ["chest", "waist"])
    reasons = []
    above_count = 0
    below_count = 0
    metrics_used = 0
    diff_ratios: List[float] = []

    for key in measurement_keys:
        body_value = getattr(profile, f"{key}_cm", None)
        min_value = getattr(entry, f"{key}_min")
        max_value = getattr(entry, f"{key}_max")
        if body_value is None or (min_value is None and max_value is None):
            continue

        metrics_used += 1
        if min_value is not None and body_value < min_value:
            below_count += 1
            diff_ratios.append((min_value - body_value) / max(min_value, 1))
            reasons.append(f"{key} is below the recommended range for size {entry.size_label}.")
        elif max_value is not None and body_value > max_value:
            above_count += 1
            diff_ratios.append((body_value - max_value) / max(max_value, 1))
            reasons.append(f"{key} is above the recommended range for size {entry.size_label}.")
        else:
            reasons.append(f"{key} is within the recommended range for size {entry.size_label}.")

    if metrics_used == 0:
        return "insufficient_data", 0.35, ["Not enough body measurements are available for a meaningful estimate."]

    average_diff = sum(diff_ratios) / len(diff_ratios) if diff_ratios else 0.0

    if above_count > 0 and not (profile.fit_preference == "slim" and average_diff < 0.03):
        fit_result = "likely_small"
    elif below_count > 0 and not (profile.fit_preference == "oversized" and average_diff < 0.03):
        fit_result = "likely_loose"
    else:
        fit_result = "likely_good"

    confidence_score = 0.45 + min(metrics_used * 0.12, 0.36)
    if source_type == "seed_estimate":
        confidence_score -= 0.08
        reasons.append("This estimate uses seeded brand-level sizing data and should be treated as directional.")

    return fit_result, round(max(0.2, min(confidence_score, 0.95)), 3), reasons


def _score_chart_entry(profile: BodyProfile, category_code: str, entry: BrandSizeChartEntry) -> Tuple[float, List[str], int]:
    measurement_keys = MEASUREMENT_PRIORITY.get(category_code, ["chest", "waist"])
    total_score = 0.0
    reasons: List[str] = []
    metrics_used = 0

    for key in measurement_keys:
        body_value = getattr(profile, f"{key}_cm", None)
        min_value = getattr(entry, f"{key}_min")
        max_value = getattr(entry, f"{key}_max")
        if body_value is None or (min_value is None and max_value is None):
            continue

        metrics_used += 1
        if min_value is not None and body_value < min_value:
            delta_ratio = (min_value - body_value) / max(min_value, 1)
            total_score += delta_ratio * 1.25
            reasons.append(f"{entry.size_label}: {key} sits a little below the target range.")
        elif max_value is not None and body_value > max_value:
            delta_ratio = (body_value - max_value) / max(max_value, 1)
            total_score += delta_ratio * 1.35
            reasons.append(f"{entry.size_label}: {key} sits a little above the target range.")
        else:
            spread = max((max_value or body_value) - (min_value or body_value), 1)
            midpoint = ((min_value or body_value) + (max_value or body_value)) / 2
            total_score += abs(body_value - midpoint) / spread * 0.25
            reasons.append(f"{entry.size_label}: {key} is comfortably inside the target range.")

    return total_score, reasons, metrics_used
