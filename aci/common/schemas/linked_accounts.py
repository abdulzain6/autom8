from datetime import datetime

from pydantic import BaseModel, ConfigDict

from aci.common.db.sql_models import  SecurityScheme
from aci.common.schemas.security_scheme import (
    APIKeySchemeCredentialsLimited,
    NoAuthSchemeCredentialsLimited,
    OAuth2SchemeCredentialsLimited,
)


class LinkedAccountCreateBase(BaseModel):
    app_name: str

class LinkedAccountOAuth2Create(LinkedAccountCreateBase):
    after_oauth2_link_redirect_url: str | None = None


class LinkedAccountAPIKeyCreate(LinkedAccountCreateBase):
    api_key: str


class LinkedAccountDefaultCreate(LinkedAccountCreateBase):
    pass


class LinkedAccountNoAuthCreate(LinkedAccountCreateBase):
    pass


class LinkedAccountUpdate(BaseModel):
    enabled: bool | None = None


class LinkedAccountOAuth2CreateState(BaseModel):
    app_name: str
    user_id: str
    client_id: str
    redirect_uri: str
    code_verifier: str
    after_oauth2_link_redirect_url: str | None = None


class LinkedAccountPublic(BaseModel):
    id: str
    app_name: str
    user_id: str
    security_scheme: SecurityScheme
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class LinkedAccountWithCredentials(LinkedAccountPublic):
    security_credentials: (
        OAuth2SchemeCredentialsLimited
        | APIKeySchemeCredentialsLimited
        | NoAuthSchemeCredentialsLimited
    )


class LinkedAccountsList(BaseModel):
    app_name: str | None = None
