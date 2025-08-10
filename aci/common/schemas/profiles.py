# aci/common/schemas/profile.py
from pydantic import BaseModel, Field
from typing import Optional

class UserProfileUpdate(BaseModel):
    """Schema for updating a user's profile."""
    name: Optional[str] = Field(None, max_length=100, description="The user's display name.")

class UserProfileResponse(BaseModel):
    """Schema for returning a user's profile."""
    id: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True