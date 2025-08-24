from __future__ import annotations
from typing import Dict, Optional, List
from pydantic import BaseModel, ConfigDict, Field

from aci.common.enums import SecurityScheme

# --- Nested Schema for App Representation ---

class AppForTemplatePublic(BaseModel):
    """A slim representation of an App for use within a Template response."""
    id: str
    name: str
    display_name: str
    logo: Optional[str] = None
    is_linked: bool = Field(False, description="Indicates if the current user has linked this app.")
    model_config = ConfigDict(from_attributes=True)
    security_scheme: Dict[SecurityScheme, dict]


# --- Main Schemas for Automation Templates ---

class AutomationTemplatePublic(BaseModel):
    """The public representation of an AutomationTemplate."""
    id: str
    name: str
    description: Optional[str] = None
    tags: List[str] = []
    goal: str
    is_deep: bool
    variable_names: List[str]
    required_apps: List[AppForTemplatePublic] = []
    all_apps_linked: bool = Field(False, description="Indicates if the user has linked all apps required by this template.")
    model_config = ConfigDict(from_attributes=True)


class AutomationTemplateUpsert(BaseModel):
    """Schema for creating or updating a template from a file."""
    name: str = Field(..., max_length=255)
    description: Optional[str] = Field(None, description="A brief description of what the template does.")
    tags: List[str] = Field(default_factory=list, description="A list of tags for categorization.")
    goal: str = Field(..., description="A Jinja2 template string.")
    variable_names: List[str] = Field(default_factory=list)
    required_app_names: List[str] = Field(
        default_factory=list,
        description="A list of App names (not IDs) required for this template.",
    )
    is_deep: bool = Field(
        default=False, description="Indicates if the template is for a deep automation.",
    )
    
    model_config = ConfigDict(from_attributes=True)


class TemplatesFile(BaseModel):
    """Schema to validate the structure of the input JSON file."""
    templates: List[AutomationTemplateUpsert]

class AutomationTemplateListParams(BaseModel):
    """Query parameters for listing automation templates."""
    category: Optional[str] = Field(None, description="Filter templates by a specific category/tag.")
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    search_query: Optional[str] = Field(None, description="A search query for full-text search across template names, descriptions, and tags.")
