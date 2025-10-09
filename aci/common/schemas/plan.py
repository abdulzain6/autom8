from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from aci.common.enums import PlanDuration


class PlanBase(BaseModel):
    """Base schema for Plan with common fields."""
    name: str = Field(..., description="Human-readable plan name")
    duration: PlanDuration = Field(..., description="Plan billing duration")
    features: List[str] = Field(..., description="List of features included in the plan")
    price: int = Field(..., description="Price in cents (smallest currency unit)")
    revenue_cat_product_id: str = Field(..., description="RevenueCat product identifier")
    automation_runs_limit: int = Field(..., description="Maximum automation runs allowed")
    voice_chat_minutes_limit: int = Field(..., description="Maximum voice chat minutes allowed")
    automations_limit: int = Field(..., description="Maximum number of automations allowed")
    description: Optional[str] = Field(None, description="Detailed plan description")
    trial_days: Optional[int] = Field(None, description="Number of trial days")
    apps_limit: Optional[int] = Field(None, description="Maximum number of apps allowed")
    active: bool = Field(True, description="Whether the plan is active")
    is_popular: bool = Field(False, description="Whether this is a popular plan")
    display_order: int = Field(0, description="Display order for UI")


class PlanCreate(PlanBase):
    """Schema for creating a new plan."""
    pass


class PlanUpdate(BaseModel):
    """Schema for updating an existing plan."""
    name: Optional[str] = None
    duration: Optional[PlanDuration] = None
    features: Optional[List[str]] = None
    price: Optional[int] = None
    revenue_cat_product_id: Optional[str] = None
    automation_runs_limit: Optional[int] = None
    voice_chat_minutes_limit: Optional[int] = None
    automations_limit: Optional[int] = None
    description: Optional[str] = None
    trial_days: Optional[int] = None
    apps_limit: Optional[int] = None
    active: Optional[bool] = None
    is_popular: Optional[bool] = None
    display_order: Optional[int] = None


class PlanResponse(PlanBase):
    """Schema for Plan responses."""
    id: str = Field(..., description="Unique plan identifier")
    created_at: datetime = Field(..., description="Plan creation timestamp")
    updated_at: datetime = Field(..., description="Plan last update timestamp")

    class Config:
        from_attributes = True


class PlanList(BaseModel):
    """Schema for listing plans."""
    plans: List[PlanResponse] = Field(..., description="List of plans")
    total: int = Field(..., description="Total number of plans")

    class Config:
        from_attributes = True


class PlanSearch(BaseModel):
    """Schema for searching plans."""
    query: Optional[str] = Field(None, description="Search query for plan name or description")
    duration: Optional[PlanDuration] = Field(None, description="Filter by plan duration")
    active_only: bool = Field(True, description="Only return active plans")
    popular_only: bool = Field(False, description="Only return popular plans")
    limit: Optional[int] = Field(None, description="Maximum number of results to return")


class PlansFile(BaseModel):
    """Schema to validate the structure of the input JSON file for plans."""
    plans: List[PlanCreate]