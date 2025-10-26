from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class UserUsageBase(BaseModel):
    """Base schema for user usage."""
    voice_agent_minutes: float = Field(ge=0, description="Minutes spent using voice agent")
    automation_runs_count: int = Field(ge=0, description="Total number of automation runs")
    successful_automation_runs: int = Field(ge=0, description="Number of successful automation runs")
    failed_automation_runs: int = Field(ge=0, description="Number of failed automation runs")
    llm_tokens_used: int = Field(ge=0, description="LLM tokens used (internal metric)")
    stt_audio_minutes: float = Field(ge=0, description="STT audio minutes processed (internal metric)")
    tts_characters_used: int = Field(ge=0, description="TTS characters used (internal metric)")


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