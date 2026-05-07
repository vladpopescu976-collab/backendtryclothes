from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageResponse(APIModel):
    message: str


class BodyProfileBase(APIModel):
    sex_fit: str = "unisex"
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    chest_cm: Optional[float] = None
    waist_cm: Optional[float] = None
    hips_cm: Optional[float] = None
    inseam_cm: Optional[float] = None
    shoulder_cm: Optional[float] = None
    fit_preference: str = "regular"


class BodyProfileUpdate(BodyProfileBase):
    pass


class BodyProfileRead(BodyProfileBase):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class UserRead(APIModel):
    id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    avatar_image_url: Optional[str] = None
    email: EmailStr
    is_active: bool
    email_verified: bool
    created_at: datetime
    updated_at: datetime
    body_profile: Optional[BodyProfileRead] = None


class BrandRead(APIModel):
    id: str
    name: str
    slug: str
    aliases_json: List[str]
    active: bool


class CategoryRead(APIModel):
    id: str
    code: str
    name: str


class SizeChartEntryRead(APIModel):
    id: str
    size_label: str
    chest_min: Optional[float]
    chest_max: Optional[float]
    waist_min: Optional[float]
    waist_max: Optional[float]
    hips_min: Optional[float]
    hips_max: Optional[float]
    inseam_min: Optional[float]
    inseam_max: Optional[float]
    fit_note: Optional[str]


class SizeChartRead(APIModel):
    id: str
    brand: BrandRead
    category: CategoryRead
    gender_fit: str
    source_type: str
    version: str
    notes: Optional[str]
    entries: List[SizeChartEntryRead]


class GarmentAssetRead(APIModel):
    id: str
    image_url: str
    detected_brand_name: Optional[str]
    detected_text: Optional[str]
    detection_confidence: Optional[float]
    brand_id: Optional[str]
    category_id: Optional[str]
    created_at: datetime


class TryOnJobRead(APIModel):
    id: str
    status: str
    person_image_url: str
    upper_garment_asset_id: Optional[str]
    lower_garment_asset_id: Optional[str]
    result_image_url: Optional[str]
    error_message: Optional[str]
    requested_generation_tier: Optional[str] = None
    final_generation_tier: Optional[str] = None
    fashn_model_used: Optional[str] = None
    credits_charged: Optional[int] = None
    openai_analysis_success: Optional[bool] = None
    fallback_used: Optional[bool] = None
    forced_premium: Optional[bool] = None
    premium_recommended: Optional[bool] = None
    fashn_job_id: Optional[str] = None
    performance: Optional["OperationPerformanceRead"] = None
    created_at: datetime
    updated_at: datetime


class TryOnVideoRead(APIModel):
    video_url: str
    duration_seconds: int
    resolution: str
    provider: str
    performance: Optional["OperationPerformanceRead"] = None


class OperationPerformanceRead(APIModel):
    upload_bytes: Optional[int] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    read_ms: Optional[int] = None
    decode_ms: Optional[int] = None
    write_ms: Optional[int] = None
    processing_ms: Optional[int] = None
    finalize_ms: Optional[int] = None
    provider_submit_ms: Optional[int] = None
    provider_poll_ms: Optional[int] = None
    response_ms: Optional[int] = None
    download_ms: Optional[int] = None
    total_ms: Optional[int] = None
    poll_count: Optional[int] = None


class TryOnVideoJobRead(APIModel):
    id: str
    status: str
    status_message: Optional[str] = None
    video_url: Optional[str] = None
    error_message: Optional[str] = None
    duration_seconds: int
    resolution: str
    provider: str
    performance: Optional[OperationPerformanceRead] = None


class BrandCandidate(APIModel):
    brand_id: str
    brand_name: str
    confidence: float


class BrandDetectResponse(APIModel):
    original_filename: str
    candidate_brands: List[BrandCandidate]
    candidate_category_code: Optional[str]
    normalized_text: str


class FitPredictionRequest(APIModel):
    brand_id: str
    category_code: str
    size_label: str
    garment_asset_id: Optional[str] = None


class FitPredictionResponse(APIModel):
    fit_result: str
    confidence_score: float
    reasons: List[str]
    brand: BrandRead
    category: CategoryRead
    size_label: str
    chart_source_type: str


class StylistRecommendationRequest(APIModel):
    prompt: str = Field(min_length=2, max_length=500)
    occasion: Optional[str] = None
    season: Optional[str] = None
    budget_level: Optional[str] = "mid"
    preferred_colors: List[str] = Field(default_factory=list)
    preferred_brands: List[str] = Field(default_factory=list)
    excluded_brands: List[str] = Field(default_factory=list)
    include_size_suggestions: bool = True


class StylistCategorySuggestion(APIModel):
    category_code: str
    label: str
    reason: str


class StylistBrandSuggestion(APIModel):
    brand_id: str
    brand_name: str
    reason: str
    aesthetic: str


class StylistSizeSuggestion(APIModel):
    brand_id: str
    brand_name: str
    category_code: str
    size_label: str
    confidence_score: float
    reasons: List[str]


class StylistOutfitIdea(APIModel):
    title: str
    summary: str
    pieces: List[str]


class StylistRecommendationResponse(APIModel):
    summary: str
    detected_style_tags: List[str]
    recommended_colors: List[str]
    search_queries: List[str]
    suggested_categories: List[StylistCategorySuggestion]
    suggested_brands: List[StylistBrandSuggestion]
    size_suggestions: List[StylistSizeSuggestion]
    outfit_ideas: List[StylistOutfitIdea]
    fit_notes: List[str]
    warnings: List[str]
