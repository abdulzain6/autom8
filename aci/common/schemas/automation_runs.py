from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field
from aci.common.db.sql_models import RunStatus


# --- Nested Schemas for Public Representation ---

class ArtifactPublic(BaseModel):
    """A public representation of an Artifact, used within run responses."""
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    
    model_config = ConfigDict(from_attributes=True)


# --- Main Schemas for Automation Runs ---

class AutomationRunPublic(BaseModel):
    """The public representation of an AutomationRun."""
    id: str
    automation_id: str
    status: RunStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    logs: Optional[dict] = None
    artifacts: List[ArtifactPublic] = []
    message: str
    model_config = ConfigDict(from_attributes=True)


class AutomationRunListParams(BaseModel):
    """Query parameters for listing and filtering automation runs."""
    status: Optional[RunStatus] = Field(
        default=None, 
        description="Filter runs by their execution status."
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of results to return.",
    )
    offset: int = Field(
        default=0, 
        ge=0, 
        description="Pagination offset."
    )

