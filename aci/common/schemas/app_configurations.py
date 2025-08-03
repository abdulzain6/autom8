from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aci.common.enums import SecurityScheme
from aci.common.schemas.security_scheme import SecuritySchemeOverrides


class AppConfigurationPublic(BaseModel):
    """The public representation of an App Configuration."""

    id: str
    app_name: str
    security_scheme: SecurityScheme
    security_scheme_overrides: SecuritySchemeOverrides
    enabled: bool
    all_functions_enabled: bool
    enabled_functions: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    # scrub the client_secret in SecuritySchemeOverrides if present
    @model_validator(mode="after")
    def scrub_client_secret(self) -> "AppConfigurationPublic":
        if self.security_scheme_overrides.oauth2:
            self.security_scheme_overrides.oauth2.client_secret = "******"
        return self


class AppConfigurationCreate(BaseModel):
    """
    Schema to create a new app configuration.
    “all_functions_enabled=True” → ignore enabled_functions.
    “all_functions_enabled=False” AND non-empty enabled_functions → selectively enable that list.
    “all_functions_enabled=False” AND empty enabled_functions → all functions disabled.
    """

    app_name: str
    security_scheme: SecurityScheme
    security_scheme_overrides: SecuritySchemeOverrides = Field(
        default_factory=SecuritySchemeOverrides
    )
    all_functions_enabled: bool = Field(default=True)
    enabled_functions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_all_functions_enabled(self) -> "AppConfigurationCreate":
        if self.all_functions_enabled and self.enabled_functions:
            raise ValueError(
                "all_functions_enabled cannot be True when enabled_functions is provided"
            )
        return self

    @model_validator(mode="after")
    def check_security_scheme_matches_override(self) -> "AppConfigurationCreate":
        if self.security_scheme_overrides.oauth2:
            if self.security_scheme != SecurityScheme.OAUTH2:
                raise ValueError(
                    f"unsupported security_scheme_overrides provided for the security scheme {self.security_scheme}"
                )
        return self


class AppConfigurationUpdate(BaseModel):
    """Schema to update an app configuration."""

    # Note: Security scheme and overrides are not updatable via this schema.
    enabled: bool | None = None
    all_functions_enabled: bool | None = None
    enabled_functions: list[str] | None = None

    @model_validator(mode="after")
    def check_all_functions_enabled(self) -> "AppConfigurationUpdate":
        # This check is only relevant if both fields are being updated in the same request
        if self.all_functions_enabled is True and self.enabled_functions is not None:
            raise ValueError(
                "all_functions_enabled cannot be set to True when updating enabled_functions"
            )
        return self


class AppConfigurationsList(BaseModel):
    """Query parameters for listing app configurations."""

    app_names: list[str] | None = Field(default=None, description="Filter by app names.")
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of results per response.",
    )
    offset: int = Field(default=0, ge=0, description="Pagination offset.")