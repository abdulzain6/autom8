import re
from datetime import datetime
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


class DefaultAppCredentialCreate(BaseModel):
    """Schema for creating default app credentials."""
    security_scheme: SecurityScheme
    credentials: APIKeySchemeCredentials | OAuth2SchemeCredentials | NoAuthSchemeCredentials


class AppUpsert(BaseModel, extra="ignore"):
    """
    Schema for creating or updating an App.
    """
    name: str
    display_name: str
    provider: str
    version: str
    description: str
    logo: str | None # Changed to optional to be more flexible
    categories: list[str]
    active: bool
    security_schemes: dict[SecurityScheme, APIKeyScheme | OAuth2Scheme | NoAuthScheme]


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
                raise ValueError(f"Invalid configuration for API_KEY scheme: {scheme_config}")
            elif scheme_type == SecurityScheme.OAUTH2 and not isinstance(
                scheme_config, OAuth2Scheme
            ):
                raise ValueError(f"Invalid configuration for OAUTH2 scheme: {scheme_config}")
            elif scheme_type == SecurityScheme.NO_AUTH and not isinstance(
                scheme_config, NoAuthScheme
            ):
                raise ValueError(f"Invalid configuration for NO_AUTH scheme: {scheme_config}")
        return v


class AppEmbeddingFields(BaseModel):
    """
    Fields used to generate app embedding.
    """
    name: str
    display_name: str
    provider: str
    description: str
    categories: list[str]


class AppsSearch(BaseModel):
    """
    Parameters for searching applications.
    """
    intent: str | None = Field(
        default=None,
        description="Natural language intent for vector similarity sorting. Results will be sorted by relevance to the intent.",
    )
    allowed_apps_only: bool = Field(
        default=False,
        description="If true, only return apps that are allowed by the agent/accessor, identified by the api key.",
    )
    include_functions: bool = Field(
        default=False,
        description="If true, include functions (name and description) of each app in the response.",
    )
    categories: list[str] | None = Field(
        default=None, description="List of categories for filtering."
    )
    limit: int = Field(
        default=100, ge=1, le=1000, description="Maximum number of Apps per response."
    )
    offset: int = Field(default=0, ge=0, description="Pagination offset.")

    @field_validator("categories")
    def validate_categories(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            v = [category for category in v if category.strip()]
            if not v:
                return None
        return v

    @field_validator("intent")
    def validate_intent(cls, v: str | None) -> str | None:
        if v is not None and v.strip() == "":
            return None
        return v


class AppsList(BaseModel):
    """
    Parameters for listing Apps.
    """
    app_names: list[str] | None = Field(default=None, description="List of app names to filter by.")
    limit: int = Field(
        default=100, ge=1, le=1000, description="Maximum number of Apps per response."
    )
    offset: int = Field(default=0, ge=0, description="Pagination offset.")


class AppBasic(BaseModel):
    """A minimal representation of an App."""
    name: str
    description: str
    functions: list[BasicFunctionDefinition] | None = None

    model_config = ConfigDict(from_attributes=True)


class AppDetails(BaseModel):
    """A detailed public representation of an App."""
    id: str
    name: str
    display_name: str
    provider: str
    version: str
    description: str
    logo: str | None
    categories: list[str]
    active: bool
    security_schemes: list[SecurityScheme]
    supported_security_schemes: SecuritySchemesPublic
    has_default_credentials: bool
    is_configured: bool
    functions: list[FunctionDetails]
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
