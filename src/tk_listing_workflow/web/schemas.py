"""
Pydantic schemas for web API.
"""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class TaskCreateResponse(BaseModel):
    """Response when creating a new task."""
    task_id: str
    position_in_queue: int
    message: str


class TaskStatusResponse(BaseModel):
    """Task status response."""
    task_id: str
    status: str
    site: str
    fission_type: str
    provider: str
    model_id: str
    count: int
    estimated_cost: float
    actual_cost: Optional[float]
    product_image_path: str
    reference_image_path: str
    result_zip_path: Optional[str]
    notes: Optional[str]
    error_message: Optional[str]
    created_at: float
    updated_at: float
    expires_at: float


class StatsSummaryResponse(BaseModel):
    """Cost statistics summary response."""
    total_images: int
    total_cost_estimated: float
    total_cost_actual: Optional[float]
    by_month: List[dict]
    by_model: List[dict]


class OptionsResponse(BaseModel):
    """Options for frontend dropdowns."""
    sites: List[str]
    fission_types: List[dict]
    models: List[dict]


class FissionType:
    SAME_PRODUCT = "same_product_fission"
    SAME_STYLE_PRODUCT_SWAP = "same_style_product_swap"
    
    @classmethod
    def choices(cls):
        return [
            {"value": cls.SAME_PRODUCT, "label": "同一产品裂变"},
            {"value": cls.SAME_STYLE_PRODUCT_SWAP, "label": "同风格换产品"},
        ]
