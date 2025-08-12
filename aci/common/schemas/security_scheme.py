from typing import Literal, TypeVar
from pydantic import BaseModel, Field, field_validator
from aci.common.enums import HttpLocation


class APIKeyScheme(BaseModel):
    location: HttpLocation = Field(
        ...,
        description="The location of the API key in the request, e.g., 'header'",
    )
    name: str = Field(
        ...,
        description="The name of the API key in the request, e.g., 'X-Subscription-Token'",
    )
    prefix: str | None = Field(
        default=None,
        description="The prefix of the API key in the request, e.g., 'Bearer'. If None, no prefix will be used.",
    )


class APIKeySchemePublic(BaseModel):
    pass


class OAuth2Scheme(BaseModel):
    location: HttpLocation = Field(
        ...,
        description="The location of the OAuth2 access token in the request, e.g., 'header'",
    )
    name: str = Field(
        ...,
        description="The name of the OAuth2 access token in the request, e.g., 'Authorization'",
    )
    prefix: str = Field(
        ...,
        description="The prefix of the OAuth2 access token in the request, e.g., 'Bearer'",
    )
    client_id: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="The client ID of the OAuth2 client (provided by ACI) used for the app",
    )
    client_secret: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="The client secret of the OAuth2 client (provided by ACI) used for the app",
    )
    scope: str = Field(
        ...,
        description="Space separated scopes of the OAuth2 client (provided by ACI) used for the app, "
        "e.g., 'openid email profile https://www.googleapis.com/auth/calendar'",
    )
    authorize_url: str = Field(
        ...,
        description="The URL of the OAuth2 authorization server, e.g., 'https://accounts.google.com/o/oauth2/v2/auth'",
    )
    access_token_url: str = Field(
        ...,
        description="The URL of the OAuth2 access token server, e.g., 'https://oauth2.googleapis.com/token'",
    )
    refresh_token_url: str = Field(
        ...,
        description="The URL of the OAuth2 refresh token server, e.g., 'https://oauth2.googleapis.com/token'",
    )
    token_endpoint_auth_method: (
        Literal["client_secret_basic", "client_secret_post"] | None
    ) = Field(
        default=None,
        description="The authentication method for the OAuth2 token endpoint, e.g., 'client_secret_post' "
        "for some providers that require client_id/client_secret to be sent in the body of the token request, like Hubspot",
    )
    redirect_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Redirect URL for OAuth2 callback.",
    )


class OAuth2SchemePublic(BaseModel):
    scope: str = Field(
        ...,
        description="Space separated scopes of the OAuth2 client used for the app, "
        "e.g., 'openid email profile https://www.googleapis.com/auth/calendar'",
    )


class OAuth2SchemeOverride(BaseModel):
    """
    Fields that are allowed to be overridden by the user.
    """

    client_id: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="The client ID of the OAuth2 client used for the app",
    )
    client_secret: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="The client secret of the OAuth2 client used for the app",
    )
    redirect_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Custom redirect URL for OAuth2 callback for complete whitelabeling. "
        "If not provided, ACI.dev's server redirect URL will be used. "
        "When user uses a custom redirect URL, their backend should forward the OAuth2 callback response to ACI.dev's callback endpoint.",
    )

    @field_validator("redirect_url")
    def validate_redirect_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # sanity check: must be http or https
        if not (v.startswith("http") or v.startswith("https")):
            raise ValueError("Redirect URL must start with http or https")
        return v

    # TODO: might need to support "scope" in the future


class NoAuthScheme(BaseModel, extra="forbid"):
    """
    model for security scheme that has no authentication.
    For now it only allows an empty dict, this is clearer and less ambiguous than using {} or None directly.
    We could also add some fields as metadata in the future if needed.
    """

    pass


class NoAuthSchemePublic(BaseModel):
    pass


class APIKeySchemeCredentials(BaseModel):
    """
    Credentials for API key scheme
    Technically this can just be a string, but we use JSON to store the credentials in the database
    for consistency and flexibility.
    """

    secret_key: str


class APIKeySchemeCredentialsLimited(BaseModel):
    """
    Limited API key credentials to expose to the client directly
    Placeholder for now just to be consistent with OAuth2SchemeCredentialsLimited
    """

    pass


class OAuth2SchemeCredentials(BaseModel):
    """Credentials for OAuth2 scheme"""

    client_id: str
    client_secret: str
    scope: str
    access_token: str
    token_type: str | None = None
    expires_at: int | None = None
    refresh_token: str | None = None
    raw_token_response: dict | None = None


class OAuth2SchemeCredentialsLimited(BaseModel):
    """Limited OAuth2 credentials to expose to the client directly"""

    access_token: str
    expires_at: int | None = None
    refresh_token: str | None = None


class NoAuthSchemeCredentials(BaseModel, extra="forbid"):
    pass


class NoAuthSchemeCredentialsLimited(BaseModel, extra="forbid"):
    """
    Limited no auth credentials to expose to the client directly
    Placeholder for now just to be consistent with OAuth2SchemeCredentialsLimited
    """

    pass


class SecuritySchemesPublic(BaseModel):
    """
    scheme_type -> scheme with sensitive information removed
    """

    api_key: APIKeySchemePublic | None = None
    oauth2: OAuth2SchemePublic | None = None
    no_auth: NoAuthSchemePublic | None = None


class SecuritySchemeOverrides(BaseModel, extra="forbid"):
    oauth2: OAuth2SchemeOverride | None = None


TScheme = TypeVar("TScheme", APIKeyScheme, OAuth2Scheme, NoAuthScheme)
TCred = TypeVar(
    "TCred", APIKeySchemeCredentials, OAuth2SchemeCredentials, NoAuthSchemeCredentials
)
