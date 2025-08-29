import re
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aci.common.enums import SecurityScheme
from aci.common.schemas.function import BasicFunctionDefinition, FunctionDetails
from aci.common.schemas.security_scheme import (
    APIKeyScheme,
    APIKeySchemeCredentials,
    NoAuthScheme,
    NoAuthSchemeCredentials,
    OAuth2Scheme,
    OAuth2SchemeCredentials,
    SecuritySchemesPublic,
)

# ==============================================================================
# Input Schemas (for creating/updating data)
# ==============================================================================

class AppUpsert(BaseModel, extra="ignore"):
    """
    Schema for creating or updating an App from a file or API call.
    """
    name: str
    display_name: str
    provider: str
    version: str
    description: str
    logo: Optional[str] = None
    categories: List[str]
    active: bool
    security_schemes: Dict[SecurityScheme, APIKeyScheme | OAuth2Scheme | NoAuthScheme]
    configuration_schema: Optional[Dict] = Field(None, description="JSON Schema for user-configurable settings.")

    @field_validator("name")
    def validate_name(cls, v: str) -> str:
        if not re.match(r"^[A-Z0-9_]+$", v) or "__" in v:
            raise ValueError(
                "name must be uppercase, contain only letters, numbers and underscores, and not have consecutive underscores"
            )
        return v

    @field_validator("security_schemes")
    def validate_security_schemes(
        cls, v: dict[SecurityScheme, APIKeyScheme | OAuth2Scheme | NoAuthScheme]
    ) -> dict[SecurityScheme, APIKeyScheme | OAuth2Scheme | NoAuthScheme]:
        for scheme_type, scheme_config in v.items():
            if scheme_type == SecurityScheme.API_KEY and not isinstance(
                scheme_config, APIKeyScheme
            ):
                raise ValueError(
                    f"Invalid configuration for API_KEY scheme: {scheme_config}"
                )
            elif scheme_type == SecurityScheme.OAUTH2 and not isinstance(
                scheme_config, OAuth2Scheme
            ):
                raise ValueError(
                    f"Invalid configuration for OAUTH2 scheme: {scheme_config}"
                )
            elif scheme_type == SecurityScheme.NO_AUTH and not isinstance(
                scheme_config, NoAuthScheme
            ):
                raise ValueError(
                    f"Invalid configuration for NO_AUTH scheme: {scheme_config}"
                )
        return v

class DefaultAppCredentialCreate(BaseModel):
    """Schema for creating default app credentials."""
    security_scheme: SecurityScheme
    credentials: (
        APIKeySchemeCredentials | OAuth2SchemeCredentials | NoAuthSchemeCredentials
    )

# ==============================================================================
# Query Parameter Schemas (for GET requests)
# ==============================================================================

class AppsSearch(BaseModel):
    """Parameters for searching applications."""
    intent: Optional[str] = Field(default=None)
    include_functions: bool = Field(default=False)
    return_automation_templates: bool = Field(default=False)
    categories: Optional[List[str]] = Field(default=None)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)

    @field_validator("categories")
    def validate_categories(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            v = [category for category in v if category.strip()]
            if not v: return None
        return v

    @field_validator("intent")
    def validate_intent(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v.strip() == "": return None
        return v

# ==============================================================================
# Output Schemas (for API responses)
# ==============================================================================

class AutomationTemplateBasic(BaseModel):
    """A slim representation of an AutomationTemplate for embedding in other responses."""
    id: str
    name: str
    description: Optional[str] = None
    tags: List[str] = []
    model_config = ConfigDict(from_attributes=True)

class AppBasic(BaseModel):
    """Basic app information, often used in lists or search results."""
    name: str
    display_name: str
    description: str
    logo: Optional[str] = None
    categories: List[str]
    active: bool
    is_linked: bool
    has_default_credentials: bool
    linked_account_id: Optional[str] = None
    security_schemes: List[SecurityScheme]
    instructions: Optional[str] = None
    functions: Optional[List[BasicFunctionDefinition]] = None
    related_automation_templates: List[AutomationTemplateBasic] = []
    model_config = ConfigDict(from_attributes=True)

class AppDetails(BaseModel):
    """Detailed information about an app, including user-specific context."""
    id: str
    name: str
    display_name: str
    provider: str
    version: str
    description: str
    logo: Optional[str] = None
    categories: List[str]
    active: bool
    security_schemes: List[SecurityScheme]
    supported_security_schemes: SecuritySchemesPublic
    has_default_credentials: bool
    is_configured: bool
    is_linked: bool
    linked_account_id: Optional[str] = None
    functions: Optional[List[FunctionDetails]] = None
    created_at: datetime
    updated_at: datetime
    instructions: Optional[str] = None
    related_automation_templates: List[AutomationTemplateBasic] = []
    configuration_schema: Optional[Dict] = None
    model_config = ConfigDict(from_attributes=True)


class AppEmbeddingFields(BaseModel):
    """
    Fields used to generate app embedding.
    """

    name: str
    display_name: str
    provider: str
    description: str
    categories: list[str]


class AppsList(BaseModel):
    """
    Parameters for listing Apps.
    """
    limit: int = Field(
        default=100, ge=1, le=1000, description="Maximum number of Apps per response."
    )
    offset: int = Field(default=0, ge=0, description="Pagination offset.")
    return_functions: bool = Field(
        default=False, description="Whether to include function details in the response."
    )
    return_automation_templates: bool = Field(
        default=False, description="Whether to include related automation templates in the response."
    )