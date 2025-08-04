from abc import abstractmethod
from typing import Any, Generic
from urllib.parse import urlparse, parse_qs

import httpx
from httpx import HTTPStatusError
from fake_useragent import UserAgent

from aci.common.db.sql_models import Function
from aci.common.logging_setup import get_logger
from aci.common.schemas.function import FunctionExecutionResult, RestMetadata
from aci.common.schemas.security_scheme import (
    TCred,
    TScheme,
)
from aci.server.function_executors.base_executor import FunctionExecutor

logger = get_logger(__name__)


class RestFunctionExecutor(FunctionExecutor[TScheme, TCred], Generic[TScheme, TCred]):
    """
    Function executor for REST functions.
    """

    @abstractmethod
    def _inject_credentials(
        self,
        security_scheme: TScheme,
        security_credentials: TCred,
        headers: dict,
        query: dict,
        body: dict,
        cookies: dict,
    ) -> None:
        pass

    def _execute(
        self,
        function: Function,
        function_input: dict,
        security_scheme: TScheme,
        security_credentials: TCred,
    ) -> FunctionExecutionResult:
        # Extract parameters by location
        path_params: dict = function_input.get("path", {})
        query_params: dict = function_input.get("query", {})
        headers: dict = function_input.get("header", {})
        cookies: dict = function_input.get("cookie", {})
        body: dict = function_input.get("body", {})

        protocol_data = RestMetadata.model_validate(function.protocol_data)

        # --- THE FIX IS HERE ---
        # The original code had a bug where httpx would overwrite query parameters
        # in the path with the ones provided in the `params` dict.
        #
        # The solution is to:
        # 1. Parse the static query parameters from the path defined in the function.
        # 2. Merge them with the dynamic query parameters from the user's input.
        # 3. Use the base path (without query string) for the URL and pass the
        #    fully merged dictionary to the `params` argument of the request.

        path_with_query = protocol_data.path
        parsed_path = urlparse(path_with_query)
        base_path = parsed_path.path
        
        # Parse the static query string from the path
        static_params = parse_qs(parsed_path.query)
        # parse_qs returns lists for values, so flatten them to single values.
        static_params_flat = {k: v[0] for k, v in static_params.items()}

        # Merge static params with dynamic params, with dynamic ones taking precedence.
        merged_query_params = {**static_params_flat, **query_params}
        
        # Construct base URL and replace any path variables (e.g., /users/{id})
        url = f"{protocol_data.server_url}{base_path}"
        if path_params:
            for path_param_name, path_param_value in path_params.items():
                url = url.replace(f"{{{path_param_name}}}", str(path_param_value))

        # If no User-Agent header is provided, add a random one.
        if not any(k.lower() == 'user-agent' for k in headers.keys()):
            try:
                ua = UserAgent()
                headers['User-Agent'] = ua.random
                logger.info(f"No User-Agent provided. Using random one: {headers['User-Agent']}")
            except Exception as e:
                logger.warning(f"Could not generate a random User-Agent: {e}. Proceeding without one.")

        self._inject_credentials(
            security_scheme, security_credentials, headers, merged_query_params, body, cookies
        )

        request = httpx.Request(
            method=protocol_data.method,
            url=url,
            params=merged_query_params if merged_query_params else None,
            headers=headers if headers else None,
            cookies=cookies if cookies else None,
            json=body if body else None,
        )

        logger.info(
            f"Executing function via raw http request, function_name={function.name}, "
            f"method={request.method} url={request.url} "
            f"Headers={request.headers}"
        )

        return self._send_request(request)

    def _send_request(self, request: httpx.Request) -> FunctionExecutionResult:
        timeout = httpx.Timeout(10.0, read=30.0)
        with httpx.Client(timeout=timeout) as client:
            try:
                response = client.send(request)
            except Exception as e:
                logger.exception(f"Failed to send function execution http request, error={e}")
                return FunctionExecutionResult(success=False, error=str(e))

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.exception(f"HTTP error occurred for function execution, error={e}")
                return FunctionExecutionResult(
                    success=False, error=self._get_error_message(response, e)
                )

            return FunctionExecutionResult(success=True, data=self._get_response_data(response))

    def _get_response_data(self, response: httpx.Response) -> Any:
        try:
            response_data = response.json() if response.content else {}
        except Exception as e:
            logger.exception(f"Error parsing function execution http response, error={e}")
            response_data = response.text

        return response_data

    def _get_error_message(self, response: httpx.Response, error: HTTPStatusError) -> str:
        try:
            return str(response.json())
        except Exception:
            return str(error)
