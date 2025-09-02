from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from aci.common.enums import RunStatus


class ActivityItem(BaseModel):
    """
    Represents a single activity item in the user's activity feed.
    """
    id: str = Field(description="Unique identifier for the activity")
    automation_id: str = Field(description="ID of the automation that was run")
    automation_name: str = Field(description="Name of the automation")
    status: RunStatus = Field(description="Status of the automation run")
    started_at: datetime = Field(description="When the automation run started")
    finished_at: Optional[datetime] = Field(description="When the automation run finished", default=None)
    message: str = Field(description="Result message or description")
    duration_seconds: Optional[float] = Field(description="Duration of the run in seconds", default=None)
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_automation_run(cls, automation_run) -> "ActivityItem":
        """
        Create an ActivityItem from an AutomationRun database object.
        """
        duration = None
        if automation_run.finished_at and automation_run.started_at:
            duration = (automation_run.finished_at - automation_run.started_at).total_seconds()
        
        return cls(
            id=automation_run.id,
            automation_id=automation_run.automation_id,
            automation_name=automation_run.automation.name,
            status=automation_run.status,
            started_at=automation_run.started_at,
            finished_at=automation_run.finished_at,
            message=automation_run.message,
            duration_seconds=duration
        )


class ActivityFeed(BaseModel):
    """
    Represents a user's activity feed with pagination.
    """
    activities: List[ActivityItem] = Field(description="List of activity items")
    total_count: int = Field(description="Total number of activities for the user")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Number of items per page")
    has_more: bool = Field(description="Whether there are more activities to load")
    
    class Config:
        from_attributes = True


class ActivityStats(BaseModel):
    """
    Represents activity statistics for a user.
    """
    total_runs: int = Field(description="Total number of automation runs")
    successful_runs: int = Field(description="Number of successful runs")
    failed_runs: int = Field(description="Number of failed runs")
    in_progress_runs: int = Field(description="Number of currently running automations")
    recent_activity_count: int = Field(description="Number of activities in the last 7 days")
    status_breakdown: dict = Field(description="Breakdown of runs by status")
    
    class Config:
        from_attributes = True


class ActivityFilters(BaseModel):
    """
    Filters for activity feed requests.
    """
    status: Optional[RunStatus] = Field(description="Filter by run status", default=None)
    limit: int = Field(description="Number of activities to return", default=20, ge=1, le=100)
    offset: int = Field(description="Number of activities to skip", default=0, ge=0)
    
    class Config:
        from_attributes = True


class ActivityResponse(BaseModel):
    """
    Standard response format for activity endpoints.
    """
    success: bool = Field(description="Whether the request was successful")
    data: Optional[ActivityFeed] = Field(description="Activity feed data", default=None)
    stats: Optional[ActivityStats] = Field(description="Activity statistics", default=None)
    message: Optional[str] = Field(description="Response message", default=None)
    
    class Config:
        from_attributes = True
