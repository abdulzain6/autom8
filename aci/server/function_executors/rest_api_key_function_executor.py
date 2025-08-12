from urllib.parse import parse_qs, urlparse
import httpx
from aci.common.db.sql_models import Function
from aci.common.enums import HttpLocation
from aci.common.exceptions import NoImplementationFound
from aci.common.logging_setup import get_logger
from aci.common.schemas.function import FunctionExecutionResult, RestMetadata
from aci.common.schemas.security_scheme import APIKeyScheme, APIKeySchemeCredentials
from aci.server.function_executors.rest_function_executor import RestFunctionExecutor

logger = get_logger(__name__)


class RestAPIKeyFunctionExecutor(RestFunctionExecutor[APIKeyScheme, APIKeySchemeCredentials]):
    """
    Function executor for API key based REST functions.
    """

    def _inject_credentials(
        self,
        security_scheme: APIKeyScheme,
        security_credentials: APIKeySchemeCredentials,
        headers: dict,
        query: dict,
        body: dict,
        cookies: dict,
    ) -> None:
        """Injects api key into the request, will modify the input dictionaries in place.
        We assume the security credentials can only be in the header, query, cookie, or body.

        Args:
            security_scheme (APIKeyScheme): The security scheme.
            security_credentials (APIKeySchemeCredentials): The security credentials.
            headers (dict): The headers dictionary.
            query (dict): The query parameters dictionary.
            cookies (dict): The cookies dictionary.
            body (dict): The body dictionary.

        Examples from app.json:
        {
            "security_schemes": {
                "api_key": {
                    "in": "header",
                    "name": "X-Test-API-Key",
                }
            },
            "default_security_credentials_by_scheme": {
                "api_key": {
                    "secret_key": "default-shared-api-key"
                }
            }
        }
        """

        security_key = (
            security_credentials.secret_key
            if not security_scheme.prefix
            else f"{security_scheme.prefix} {security_credentials.secret_key}"
        )

        match security_scheme.location:
            case HttpLocation.HEADER:
                headers[security_scheme.name] = security_key
            case HttpLocation.QUERY:
                query[security_scheme.name] = security_key
            case HttpLocation.BODY:
                body[security_scheme.name] = security_key
            case HttpLocation.COOKIE:
                cookies[security_scheme.name] = security_key
            case _:
                # should never happen
                logger.error(f"Unsupported API key location, location={security_scheme.location}")
                raise NoImplementationFound(
                    f"Unsupported API key location, location={security_scheme.location}"
                )

    @staticmethod
    def test_credentials(
        test_function: Function,
        security_scheme: APIKeyScheme,
        security_credentials: APIKeySchemeCredentials,
    ) -> FunctionExecutionResult:
        """
        A static utility to test API key credentials without needing an executor instance.
        """
        # This logic is adapted from the main _execute method
        protocol_data = RestMetadata.model_validate(test_function.protocol_data)
        
        # Test functions are assumed to have no dynamic input
        path_params, query_params, headers, cookies, body = {}, {}, {}, {}, {}

        # Construct URL and handle path/query parameters
        parsed_path = urlparse(protocol_data.path)
        base_path = parsed_path.path
        static_params = {k: v[0] for k, v in parse_qs(parsed_path.query).items()}
        url = f"{protocol_data.server_url}{base_path}"

        # Inject credentials into the request dictionaries
        # Note: We are calling the existing _inject_credentials method but passing None for 'self'
        # because its logic doesn't depend on the instance state.
        RestAPIKeyFunctionExecutor._inject_credentials(
            None, security_scheme, security_credentials, headers, static_params, body, cookies # type: ignore
        )

        # Build and send the request using httpx
        request = httpx.Request(
            method=protocol_data.method,
            url=url,
            params=static_params or None,
            headers=headers or None,
            cookies=cookies or None,
            json=body or None,
        )

        logger.info(f"Executing credential test request, method={request.method} url={request.url}")

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.send(request)
                response.raise_for_status()
                return FunctionExecutionResult(success=True)
        except httpx.HTTPStatusError as e:
            error_msg = str(e.response.json()) if e.response.content else str(e)
            logger.error(f"Credential test HTTP error: {error_msg}")
            return FunctionExecutionResult(success=False, error=error_msg)
        except Exception as e:
            logger.exception(f"Credential test failed with an exception: {e}")
            return FunctionExecutionResult(success=False, error=str(e))