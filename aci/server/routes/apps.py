from typing import Annotated

from fastapi import APIRouter, Depends, Query
from openai import OpenAI

from aci.common.db import crud
from aci.common.embeddings import generate_embedding
from aci.common.enums import SecurityScheme
from aci.common.exceptions import AppNotFound
from aci.common.logging_setup import get_logger
from aci.common.schemas.app import (
    AppBasic,
    AppDetails,
    AppsList,
    AppsSearch,
)
from aci.common.schemas.function import BasicFunctionDefinition, FunctionDetails
from aci.common.schemas.security_scheme import SecuritySchemesPublic
from aci.server import config
from aci.server import dependencies as deps

logger = get_logger(__name__)
router = APIRouter()
openai_client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)


@router.get("", response_model_exclude_none=True)
def list_apps(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[AppsList, Depends()],
) -> list[AppDetails]:
    """
    Get a list of Apps and their details. Sorted by App name.
    """
    app_linked_account_pairs = crud.apps.list_apps_with_user_context(
        db=context.db_session,
        user_id=context.user.id,
        active_only=True,
        configured_only=True,
        app_names=query_params.app_names,
        limit=query_params.limit,
        offset=query_params.offset,
    )

    response: list[AppDetails] = []
    for app, linked_account in app_linked_account_pairs:
        app_details = AppDetails(
            id=app.id,
            name=app.name,
            display_name=app.display_name,
            provider=app.provider,
            version=app.version,
            description=app.description,
            logo=app.logo,
            categories=app.categories,
            active=app.active,
            security_schemes=list(app.security_schemes.keys()),
            supported_security_schemes=SecuritySchemesPublic.model_validate(
                app.security_schemes
            ),
            has_default_credentials=app.has_default_credentials,
            functions=[
                FunctionDetails.model_validate(function) for function in app.functions
            ],
            created_at=app.created_at,
            updated_at=app.updated_at,
            is_configured=app.has_configuration,
            is_linked=linked_account is not None,
            linked_account_id=linked_account.id if linked_account else None,
            instructions=app.security_schemes.get(SecurityScheme.API_KEY, {}).get(
                "instructions", None
            ),
        )
        response.append(app_details)

    return response


@router.get("/search", response_model_exclude_none=True)
def search_apps(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[AppsSearch, Depends()],  # Use Depends()
) -> list[AppBasic]:
    """
    Search for Apps.
    Intented to be used by agents to search for apps based on natural language intent.
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

    # --- EFFICIENT SINGLE QUERY ---
    apps_with_context = crud.apps.search_apps(
        db_session=context.db_session,
        user_id=context.user.id,
        active_only=True,
        configured_only=True,
        app_names=None,
        categories=query_params.categories,
        intent_embedding=intent_embedding,
        limit=query_params.limit,
        offset=query_params.offset,
    )

    response: list[AppBasic] = []
    for app, linked_account, _ in apps_with_context:
        app_data = {
            "name": app.name,
            "description": app.description,
            "logo": app.logo,
            "is_linked": linked_account is not None,
            "categories": app.categories,
            "active": app.active,
            "display_name": app.display_name,
            "has_default_credentials": app.has_default_credentials,
            "linked_account_id": linked_account.id if linked_account else None,
            "security_schemes": list(app.security_schemes.keys()),
            "instructions": app.security_schemes.get(SecurityScheme.API_KEY, {}).get(
                "instructions", None
            ),
        }
        if query_params.include_functions:
            app_data["functions"] = [
                BasicFunctionDefinition(
                    name=function.name, description=function.description
                )
                for function in app.functions
            ]

        response.append(AppBasic(**app_data))

    logger.info(
        "Search apps result",
        extra={
            "search_apps": {
                "query_params_json": query_params.model_dump_json(),
                "app_names": [app.name for app, _, __ in apps_with_context],
            },
        },
    )
    return response


@router.get("/{app_name}", response_model_exclude_none=True)
def get_app_details(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    app_name: str,
    include_functions: bool = Query(
        True, description="Whether to include function details in the response."
    ),
) -> AppDetails:
    """
    Returns an application (name, description, and functions).
    """
    result = crud.apps.get_app_with_user_context(
        db_session=context.db_session,
        app_name=app_name,
        user_id=context.user.id,
        active_only=True,
    )

    if not result:
        logger.error(f"App not found, app_name={app_name}")
        raise AppNotFound(f"App={app_name} not found")

    app, linked_account = result

    functions = [function for function in app.functions if function.active]

    app_details = AppDetails(
        id=app.id,
        name=app.name,
        display_name=app.display_name,
        provider=app.provider,
        version=app.version,
        description=app.description,
        logo=app.logo,
        categories=app.categories,
        active=app.active,
        security_schemes=list(app.security_schemes.keys()),
        supported_security_schemes=SecuritySchemesPublic.model_validate(
            app.security_schemes
        ),
        has_default_credentials=app.has_default_credentials,
        is_configured=app.has_configuration,
        is_linked=linked_account is not None,
        functions=(
            [FunctionDetails.model_validate(function) for function in functions]
            if include_functions
            else None
        ),
        created_at=app.created_at,
        updated_at=app.updated_at,
        linked_account_id=linked_account.id if linked_account else None,
        instructions=app.security_schemes.get(SecurityScheme.API_KEY, {}).get(
            "instructions", None
        ),
    )
    return app_details
