import random
import string
import time
from typing import Any, cast

from authlib.integrations.httpx_client import AsyncOAuth2Client

from aci.common.exceptions import OAuth2Error
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import OAuth2SchemeCredentials

UNICODE_ASCII_CHARACTER_SET = string.ascii_letters + string.digits
OAUTH_APPS_REQUIRE_CREDENTIALS_IN_BODY = [
    "TYPEFORM",
    "WORDPRESS"
]

logger = get_logger(__name__)



class OAuth2Manager:
    def __init__(
        self,
        app_name: str,
        client_id: str,
        client_secret: str,
        scope: str,
        authorize_url: str,
        access_token_url: str,
        refresh_token_url: str,
        token_endpoint_auth_method: str | None = None,
    ):
        """
        Initialize the OAuth2Manager
        """
        self.app_name = app_name
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.authorize_url = authorize_url
        self.access_token_url = access_token_url
        self.refresh_token_url = refresh_token_url
        self.token_endpoint_auth_method = token_endpoint_auth_method

        self.oauth2_client = AsyncOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=token_endpoint_auth_method,
            code_challenge_method="S256",
            update_token=None,
        )

    async def create_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        code_verifier: str,
        access_type: str = "offline",
        prompt: str = "consent",
    ) -> str:
        """
        Create authorization URL for user to authorize your application
        """
        app_specific_params = {}
        if self.app_name == "REDDIT":
            app_specific_params = {"duration": "permanent"}
            logger.info(
                f"Adding app specific params, app_name={self.app_name}, params={app_specific_params}"
            )

        authorization_url, _ = self.oauth2_client.create_authorization_url(
            url=self.authorize_url,
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=code_verifier,
            access_type=access_type,
            prompt=prompt,
            scope=self.scope,
            **app_specific_params,
        )

        return str(authorization_url)

    async def fetch_token(
        self,
        redirect_uri: str,
        code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        """
        Exchange authorization code for access token, with provider-specific logic.
        """
        try:
            extra_params = {}
            if self.app_name in OAUTH_APPS_REQUIRE_CREDENTIALS_IN_BODY:
                logger.info(f"Applying specific token request params for {self.app_name}")
                extra_params = {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }

            token = cast(
                dict[str, Any],
                await self.oauth2_client.fetch_token(
                    self.access_token_url,
                    redirect_uri=redirect_uri,
                    code=code,
                    code_verifier=code_verifier,
                    **extra_params,  # Splat the extra params here
                ),
            )
            return token
        except Exception as e:
            logger.error(f"Failed to fetch access token, app_name={self.app_name}, error={e}")
            raise OAuth2Error("failed to fetch access token") from e

    async def refresh_token(
        self,
        refresh_token: str,
    ) -> dict[str, Any]:
        try:
            token = cast(
                dict[str, Any],
                await self.oauth2_client.refresh_token(
                    self.refresh_token_url, refresh_token=refresh_token
                ),
            )
            return token
        except Exception as e:
            logger.error(f"Failed to refresh access token, app_name={self.app_name}, error={e}")
            raise OAuth2Error("Failed to refresh access token") from e

    def parse_fetch_token_response(self, token: dict) -> OAuth2SchemeCredentials:
        """
        Parse OAuth2SchemeCredentials from token response with app-specific handling.
        """
        data = token

        if self.app_name == "SLACK":
            if "authed_user" in data:
                data = cast(dict, data["authed_user"])
            else:
                logger.error(f"Missing authed_user in Slack OAuth response, app={self.app_name}")
                raise OAuth2Error("Missing access_token in Slack OAuth response")

        if "access_token" not in data:
            logger.error(f"Missing access_token in OAuth response, app={self.app_name}")
            logger.info(f"OAuth response data: {data}")
            raise OAuth2Error("Missing access_token in OAuth response")

        expires_at: int | None = None
        if "expires_at" in data:
            expires_at = int(data["expires_at"])
        elif "expires_in" in data:
            expires_at = int(time.time()) + int(data["expires_in"])

        return OAuth2SchemeCredentials(
            client_id=self.client_id,
            client_secret=self.client_secret,
            scope=self.scope,
            access_token=data["access_token"],
            token_type=data.get("token_type"),
            expires_at=expires_at,
            refresh_token=data.get("refresh_token"),
            raw_token_response=token,
        )

    @staticmethod
    def generate_code_verifier(length: int = 48) -> str:
        """
        Generate a random code verifier for OAuth2
        """
        rand = random.SystemRandom()
        return "".join(rand.choice(UNICODE_ASCII_CHARACTER_SET) for _ in range(length))

    @staticmethod
    def rewrite_oauth2_authorization_url(app_name: str, authorization_url: str) -> str:
        """
        Rewrite OAuth2 authorization URL for specific apps that need special handling.
        """
        if app_name == "SLACK":
            if "scope=" in authorization_url:
                scope_start = authorization_url.find("scope=") + 6
                scope_end = authorization_url.find("&", scope_start)
                if scope_end == -1:
                    scope_end = len(authorization_url)
                original_scope = authorization_url[scope_start:scope_end]

                new_url = authorization_url.replace(
                    f"scope={original_scope}", f"user_scope={original_scope}&scope="
                )
                return new_url

        return authorization_url

