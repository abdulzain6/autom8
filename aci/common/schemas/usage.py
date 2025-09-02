from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class UserUsageBase(BaseModel):
    """Base schema for user usage."""
    year: int = Field(description="Year of the usage period")
    month: int = Field(ge=1, le=12, description="Month of the usage period (1-12)")
    voice_agent_minutes: float = Field(ge=0, description="Minutes spent using voice agent")
    automation_runs_count: int = Field(ge=0, description="Total number of automation runs")
    successful_automation_runs: int = Field(ge=0, description="Number of successful automation runs")
    failed_automation_runs: int = Field(ge=0, description="Number of failed automation runs")
    llm_tokens_used: int = Field(ge=0, description="LLM tokens used (internal metric)")
    stt_audio_minutes: float = Field(ge=0, description="STT audio minutes processed (internal metric)")
    tts_characters_used: int = Field(ge=0, description="TTS characters used (internal metric)")


class UserUsageCreate(UserUsageBase):
    """Schema for creating user usage record."""
    user_id: str


class UserUsageUpdate(BaseModel):
    """Schema for updating user usage record."""
    voice_agent_minutes: Optional[float] = None
    automation_runs_count: Optional[int] = None
    successful_automation_runs: Optional[int] = None
    failed_automation_runs: Optional[int] = None
    llm_tokens_used: Optional[int] = None
    stt_audio_minutes: Optional[float] = None
    tts_characters_used: Optional[int] = None


class UserUsageResponse(UserUsageBase):
    """Schema for user usage response."""
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate for automation runs."""
        if self.automation_runs_count == 0:
            return 0.0
        return (self.successful_automation_runs / self.automation_runs_count) * 100

    class Config:
        from_attributes = True


class MonthlyUsageStats(BaseModel):
    """Schema for monthly usage statistics."""
    # User-facing metrics
    voice_minutes: float
    automation_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float
    
    # Internal cost metrics
    llm_tokens: int
    stt_minutes: float
    tts_characters: int


class UserUsageStats(BaseModel):
    """Schema for aggregated user usage statistics."""
    # User-facing totals
    total_voice_minutes: float
    total_automation_runs: int
    total_successful_runs: int
    total_failed_runs: int
    total_automations_created: int
    success_rate: float
    
    # Internal cost totals
    total_llm_tokens: int
    total_stt_minutes: float
    total_tts_characters: int
    
    current_month: MonthlyUsageStats


class UserUsageHistory(BaseModel):
    """Schema for user usage history response."""
    usage_records: List[UserUsageResponse]
    total_records: int
    stats: UserUsageStats


class UsageIncrementRequest(BaseModel):
    """Schema for incrementing usage metrics."""
    # User-facing metrics
    minutes: Optional[float] = Field(None, ge=0, description="Minutes to add to voice agent usage")
    automation_run: Optional[bool] = Field(None, description="Whether to increment automation run count")
    automation_success: Optional[bool] = Field(True, description="Whether the automation run was successful")
    
    # Internal cost metrics
    llm_tokens: Optional[int] = Field(None, ge=0, description="LLM tokens to add")
    stt_minutes: Optional[float] = Field(None, ge=0, description="STT audio minutes to add")
    tts_characters: Optional[int] = Field(None, ge=0, description="TTS characters to add")
    
    # Time period
    month: Optional[int] = Field(None, ge=1, le=12, description="Specific month (defaults to current)")
    year: Optional[int] = Field(None, ge=2000, description="Specific year (defaults to current)")
