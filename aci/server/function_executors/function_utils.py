from datetime import UTC, datetime
from typing import Annotated
from fastapi import Depends, Query
from openai import OpenAI
from sqlalchemy.orm import Session

from aci.common import processor
from aci.common.db import crud
from aci.common.db.sql_models import Function
from aci.common.embeddings import generate_embedding
from aci.common.enums import FunctionDefinitionFormat
from aci.common.exceptions import (
    AppConfigurationDisabled,
    AppConfigurationNotFound,
    FunctionNotFound,
    InvalidFunctionDefinitionFormat,
    LinkedAccountNotFound,
)
from aci.common.logging_setup import get_logger
from aci.common.schemas.function import (
    AnthropicFunctionDefinition,
    BasicFunctionDefinition,
    FunctionExecutionResult,
    FunctionsSearch,
    OpenAIFunction,
    OpenAIFunctionDefinition,
    OpenAIResponsesFunctionDefinition,
)
from aci.server import config
from aci.server import dependencies as deps
from aci.server import security_credentials_manager as scm
from aci.server.function_executors import get_executor
from aci.server.security_credentials_manager import SecurityCredentialsResponse


logger = get_logger(__name__)
openai_client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)


def format_function_definition(
    function: Function, format: FunctionDefinitionFormat
) -> (
    BasicFunctionDefinition
    | OpenAIFunctionDefinition
    | OpenAIResponsesFunctionDefinition
    | AnthropicFunctionDefinition
):
    match format:
        case FunctionDefinitionFormat.BASIC:
            return BasicFunctionDefinition(
                name=function.name,
                description=function.description,
            )
        case FunctionDefinitionFormat.OPENAI:
            return OpenAIFunctionDefinition(
                function=OpenAIFunction(
                    name=function.name,
                    description=function.description,
                    parameters=processor.filter_visible_properties(function.parameters),
                )
            )
        case FunctionDefinitionFormat.OPENAI_RESPONSES:
            return OpenAIResponsesFunctionDefinition(
                type="function",
                name=function.name,
                description=function.description,
                parameters=processor.filter_visible_properties(function.parameters),
            )
        case FunctionDefinitionFormat.ANTHROPIC:
            return AnthropicFunctionDefinition(
                name=function.name,
                description=function.description,
                input_schema=processor.filter_visible_properties(function.parameters),
            )
        case _:
            raise InvalidFunctionDefinitionFormat(f"Invalid format: {format}")


def execute_function(
    db_session: Session,
    function_name: str,
    user_id: str,
    function_input: dict,
    run_id: str | None = None,
) -> FunctionExecutionResult:
    """
    Execute a function with the given parameters.

    Args:
        db_session: Database session
        project: Project object
        agent: Agent object
        function_name: Name of the function to execute
        function_input: Input parameters for the function
        linked_account_owner_id: ID of the linked account owner

    Returns:
        FunctionExecutionResult: Result of the function execution

    Raises:
        FunctionNotFound: If the function is not found
        AppConfigurationNotFound: If the app configuration is not found
        AppConfigurationDisabled: If the app configuration is disabled
        AppNotAllowedForThisAgent: If the app is not allowed for the agent
        LinkedAccountNotFound: If the linked account is not found
        LinkedAccountDisabled: If the linked account is disabled
    """
    # Get the function
    function = crud.functions.get_function(
        db_session,
        function_name,
        True,
    )
    if not function:
        logger.error(
            f"Failed to execute function, function not found, function_name={function_name}"
        )
        raise FunctionNotFound(f"function={function_name} not found")

    # Check if the App (that this function belongs to) is configured
    app_configuration = crud.app_configurations.get_app_configuration(
        db_session, function.app.name
    )
    if not app_configuration:
        logger.error(
            f"Failed to execute function, app configuration not found, "
            f"function_name={function_name} app_name={function.app.name}"
        )
        raise AppConfigurationNotFound(
            f"Configuration for app={function.app.name} not found"
        )
    # Check if user has disabled the app configuration
    if not app_configuration.enabled:
        logger.error(
            f"Failed to execute function, app configuration is disabled, "
            f"function_name={function_name} app_name={function.app.name} app_configuration_id={app_configuration.id}"
        )
        raise AppConfigurationDisabled(
            f"Configuration for app={function.app.name} is disabled"
        )

    # Check if the linked account status (configured, enabled, etc.)
    linked_account = crud.linked_accounts.get_linked_account(
        db_session,
        user_id,
        function.app.name,
    )
    if not linked_account:
        logger.error(
            f"Failed to execute function, linked account not found, "
            f"function_name={function_name} app_name={function.app.name} user_id={user_id}"
        )
        raise LinkedAccountNotFound(
            f"Linked account with user_id={user_id} not found for app={function.app.name},"
            f"please link the account for this app"
        )

    security_credentials_response: SecurityCredentialsResponse = (
        scm.get_security_credentials(
            app_configuration.app, app_configuration, linked_account
        )
    )

    scm.update_security_credentials(
        db_session, function.app, linked_account, security_credentials_response
    )

    logger.info(
        f"Fetched security credentials for function execution, function_name={function_name}, "
        f"app_name={function.app.name}, user_id={user_id}, "
        f"linked_account_id={linked_account.id}, is_updated={security_credentials_response.is_updated}, "
    )
    db_session.commit()

    function_executor = get_executor(function.protocol, linked_account, run_id=run_id)
    logger.info(
        f"Instantiated function executor, function_executor={type(function_executor)}, "
        f"function={function_name}"
    )

    # Execute the function
    execution_result = function_executor.execute(
        function,
        function_input,
        security_credentials_response.scheme,
        security_credentials_response.credentials,
    )

    last_used_at: datetime = datetime.now(UTC)
    crud.linked_accounts.update_linked_account_last_used_at(
        db_session,
        last_used_at,
        linked_account,
    )
    db_session.commit()

    if not execution_result.success:
        logger.error(
            f"Function execution result error, function_name={function_name}, "
            f"error={execution_result.error}"
        )

    return execution_result


def get_functions_definitions(
    db_session: Session,
    function_names: list[str],
    format: FunctionDefinitionFormat = FunctionDefinitionFormat.BASIC,
) -> list[
    BasicFunctionDefinition
    | OpenAIFunctionDefinition
    | OpenAIResponsesFunctionDefinition
    | AnthropicFunctionDefinition
]:
    """
    Get function definitions for a list of function names.

    Args:
        db_session: Database session
        function_names: List of function names to get definitions for
        format: Format of the function definition to return

    Returns:
        List of function definitions in the requested format
    """
    # Query functions by name
    functions = (
        db_session.query(Function).filter(Function.name.in_(function_names)).all()
    )

    # Get function definitions
    function_definitions = []
    for function in functions:
        function_definition = format_function_definition(function, format)
        function_definitions.append(function_definition)

    return function_definitions


def search_functions(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[FunctionsSearch, Query()],
) -> list[
    BasicFunctionDefinition
    | OpenAIFunctionDefinition
    | OpenAIResponsesFunctionDefinition
    | AnthropicFunctionDefinition
]:
    """
    Returns the basic information of a list of functions.
    """
    intent_embedding = (
        generate_embedding(
            openai_client,
            config.OPENAI_EMBEDDING_MODEL,
            config.OPENAI_EMBEDDING_DIMENSION,
            query_params.intent,
        )
        if query_params.intent
        else None
    )
    logger.debug(
        f"Generated intent embedding, intent={query_params.intent}, intent_embedding={intent_embedding}"
    )

    if query_params.app_names is None:
        apps_to_filter = None
    else:
        apps_to_filter = query_params.app_names

    functions = crud.functions.search_functions(
        context.db_session,
        True,
        apps_to_filter,
        intent_embedding,
        query_params.limit,
        query_params.offset,
    )

    logger.info(
        "Search functions result",
        extra={
            "search_functions": {
                "query_params_json": query_params.model_dump_json(),
                "function_names": [function.name for function in functions],
            }
        },
    )
    function_definitions = [
        format_function_definition(function, query_params.format)
        for function in functions
    ]

    return function_definitions


def get_function_definition(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    function_name: str,
    format: FunctionDefinitionFormat = Query(  # noqa: B008 # TODO: need to fix this later
        default=FunctionDefinitionFormat.OPENAI,
        description="The format to use for the function definition (e.g., 'openai' or 'anthropic'). "
        "There is also a 'basic' format that only returns name and description.",
    ),
) -> (
    BasicFunctionDefinition
    | OpenAIFunctionDefinition
    | OpenAIResponsesFunctionDefinition
    | AnthropicFunctionDefinition
):
    """
    Return the function definition that can be used directly by LLM.
    The actual content depends on the FunctionDefinitionFormat and the function itself.
    """
    function: Function | None = crud.functions.get_function(
        context.db_session,
        function_name,
        True,
    )
    if not function:
        logger.error(
            f"Failed to get function definition, function not found, function_name={function_name}"
        )
        raise FunctionNotFound(f"function={function_name} not found")

    function_definition = format_function_definition(function, format)

    logger.info(
        "function definition to return",
        extra={
            "get_function_definition": {
                "format": format,
                "function_name": function_name,
            },
        },
    )
    return function_definition
