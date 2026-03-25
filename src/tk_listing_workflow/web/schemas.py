from __future__ import annotations

from pydantic import BaseModel, Field


class ModelOption(BaseModel):
    model_id: str
    label: str
    price_per_image: float


class FissionTypeOption(BaseModel):
    value: str
    label: str
    experimental: bool = False


class OptionsResponse(BaseModel):
    groups: list[str]
    sites: list[str]
    fission_types: list[FissionTypeOption]
    models: list[ModelOption]


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    position_in_queue: int = 0
    deduplicated: bool = False
    experimental: bool = False


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    queue_position: int = 0
    site: str
    fission_type: str
    group_name: str
    operator_name: str = ""
    model_id: str
    count: int
    estimated_cost: float
    actual_cost: float | None = None
    result_count: int = 0
    download_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    experimental: bool = False
    error_message: str = ""
    created_at: float
    updated_at: float


class StatsSummaryResponse(BaseModel):
    total_images: int = 0
    total_cost_estimated: float = Field(default=0)
    total_cost_actual: float = Field(default=0)
    by_month: list[dict] = Field(default_factory=list)
    by_model: list[dict] = Field(default_factory=list)
    by_group: list[dict] = Field(default_factory=list)
