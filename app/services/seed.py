from __future__ import annotations

from copy import deepcopy
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.brand import Brand
from app.models.brand_size_chart import BrandSizeChart
from app.models.brand_size_chart_entry import BrandSizeChartEntry
from app.models.category import Category

BRAND_DEFINITIONS = [
    {"name": "Zara", "slug": "zara", "aliases": ["zaraman", "zara woman", "zara basic"]},
    {"name": "Nike", "slug": "nike", "aliases": ["nike sportswear", "nikelab", "nike sb"]},
    {"name": "H&M", "slug": "h-and-m", "aliases": ["hm", "h&m", "divided"]},
    {"name": "Bershka", "slug": "bershka", "aliases": ["bsk"]},
    {"name": "Pull&Bear", "slug": "pull-and-bear", "aliases": ["pull&bear", "pull bear", "p&b"]},
]

CATEGORY_DEFINITIONS = [
    {"code": "tshirt", "name": "T-Shirt"},
    {"code": "hoodie", "name": "Hoodie"},
    {"code": "pants", "name": "Pants"},
    {"code": "jeans", "name": "Jeans"},
]

UPPER_TEMPLATE = [
    {"size_label": "XS", "chest_min": 84, "chest_max": 89, "waist_min": 70, "waist_max": 75},
    {"size_label": "S", "chest_min": 90, "chest_max": 95, "waist_min": 76, "waist_max": 81},
    {"size_label": "M", "chest_min": 96, "chest_max": 101, "waist_min": 82, "waist_max": 87},
    {"size_label": "L", "chest_min": 102, "chest_max": 107, "waist_min": 88, "waist_max": 94},
    {"size_label": "XL", "chest_min": 108, "chest_max": 114, "waist_min": 95, "waist_max": 101},
]

LOWER_TEMPLATE = [
    {"size_label": "XS", "waist_min": 72, "waist_max": 76, "hips_min": 87, "hips_max": 91, "inseam_min": 76, "inseam_max": 79},
    {"size_label": "S", "waist_min": 77, "waist_max": 81, "hips_min": 92, "hips_max": 96, "inseam_min": 78, "inseam_max": 80},
    {"size_label": "M", "waist_min": 82, "waist_max": 86, "hips_min": 97, "hips_max": 101, "inseam_min": 79, "inseam_max": 82},
    {"size_label": "L", "waist_min": 87, "waist_max": 92, "hips_min": 102, "hips_max": 106, "inseam_min": 81, "inseam_max": 84},
    {"size_label": "XL", "waist_min": 93, "waist_max": 98, "hips_min": 107, "hips_max": 112, "inseam_min": 82, "inseam_max": 85},
]

BRAND_SHIFTS = {
    "zara": {"upper": 0.0, "lower": 0.0},
    "nike": {"upper": 2.0, "lower": 1.0},
    "h-and-m": {"upper": 1.0, "lower": 1.0},
    "bershka": {"upper": -1.0, "lower": -1.0},
    "pull-and-bear": {"upper": 0.5, "lower": 0.5},
}


def seed_reference_data(db: Session) -> None:
    categories = _ensure_categories(db)
    brands = _ensure_brands(db)

    for brand in brands:
        shift = BRAND_SHIFTS.get(brand.slug, {"upper": 0.0, "lower": 0.0})
        for category in categories:
            chart = (
                db.query(BrandSizeChart)
                .filter(
                    BrandSizeChart.brand_id == brand.id,
                    BrandSizeChart.category_id == category.id,
                    BrandSizeChart.gender_fit == "unisex",
                )
                .first()
            )
            if chart:
                continue

            chart = BrandSizeChart(
                brand_id=brand.id,
                category_id=category.id,
                gender_fit="unisex",
                source_type="seed_estimate",
                version="v1",
                notes="Initial seeded size chart for MVP fit estimation.",
            )
            db.add(chart)
            db.flush()

            template = UPPER_TEMPLATE if category.code in {"tshirt", "hoodie"} else LOWER_TEMPLATE
            delta = shift["upper"] if category.code in {"tshirt", "hoodie"} else shift["lower"]
            for entry_data in _shift_template(template, delta):
                chart.entries.append(BrandSizeChartEntry(**entry_data))

    db.commit()


def _ensure_categories(db: Session) -> List[Category]:
    existing = {category.code: category for category in db.query(Category).all()}
    categories = []
    for definition in CATEGORY_DEFINITIONS:
        category = existing.get(definition["code"])
        if not category:
            category = Category(**definition)
            db.add(category)
            db.flush()
        categories.append(category)
    return categories


def _ensure_brands(db: Session) -> List[Brand]:
    existing = {brand.slug: brand for brand in db.query(Brand).all()}
    brands = []
    for definition in BRAND_DEFINITIONS:
        brand = existing.get(definition["slug"])
        if not brand:
            brand = Brand(
                name=definition["name"],
                slug=definition["slug"],
                aliases_json=definition["aliases"],
                active=True,
            )
            db.add(brand)
            db.flush()
        brands.append(brand)
    return brands


def _shift_template(template: List[Dict[str, float]], delta: float) -> List[Dict[str, float]]:
    shifted_entries = []
    for entry in template:
        shifted = deepcopy(entry)
        for key in (
            "chest_min",
            "chest_max",
            "waist_min",
            "waist_max",
            "hips_min",
            "hips_max",
            "inseam_min",
            "inseam_max",
        ):
            if key in shifted and shifted[key] is not None:
                shifted[key] = round(shifted[key] + delta, 1)
        shifted["fit_note"] = "Seeded estimate. Replace with official chart when available."
        shifted_entries.append(shifted)
    return shifted_entries

