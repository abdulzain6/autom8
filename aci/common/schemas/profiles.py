from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# Shared properties for a user profile
class UserProfileBase(BaseModel):
    name: Optional[str] = Field(None, max_length=100, description="The user's display name.")
    avatar_url: Optional[str] = Field(None, max_length=255, description="URL for the user's avatar image.")


# Properties to receive on profile creation
class UserProfileCreate(UserProfileBase):
    # No extra fields needed for creation beyond the base
    pass


# Properties to receive on profile update
class UserProfileUpdate(UserProfileBase):
    # All fields are optional for partial updates
    name: Optional[str] | None = None
    avatar_url: Optional[str] | None = None


# Properties to return to the client (public-facing model)
class UserProfilePublic(UserProfileBase):
    id: str
    
    # Use from_attributes=True to allow Pydantic to read data from ORM models
    model_config = ConfigDict(from_attributes=True)
