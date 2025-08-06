from typing import Annotated

from fastapi import APIRouter, Depends, Query
from openai import OpenAI

from aci.common.db import crud
from aci.common.embeddings import generate_embedding
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
async def list_apps(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[AppsList, Query()],
) -> list[AppDetails]:
    """
    Get a list of Apps and their details. Sorted by App name.
    """
    apps = crud.apps.get_apps(
        context.db_session,
        True,
        True,
        query_params.app_names,
        query_params.limit,
        query_params.offset,
    )

    response: list[AppDetails] = []
    for app in apps:
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
            supported_security_schemes=SecuritySchemesPublic.model_validate(app.security_schemes),
            has_default_credentials=app.has_default_credentials,
            functions=[FunctionDetails.model_validate(function) for function in app.functions],
            created_at=app.created_at,
            updated_at=app.updated_at,
            is_configured=app.has_configuration,
            is_linked=app.has_linked_account(context.user.id),
        )
        response.append(app_details)

    return response


@router.get("/search", response_model_exclude_none=True)
async def search_apps(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[AppsSearch, Query()],
) -> list[AppBasic]:
    """
    Search for Apps.
    Intented to be used by agents to search for apps based on natural language intent.
    """
    # TODO: currently the search is done across all apps, we might want to add flags to account for below scenarios:
    # - when clients search for apps, if an app is configured but disabled by client, should it be discoverable?
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
    apps_with_scores = crud.apps.search_apps(
        context.db_session,
        True,
        True,
        None,
        query_params.categories,
        intent_embedding,
        query_params.limit,
        query_params.offset,
    )

    apps: list[AppBasic] = []

    for app, _ in apps_with_scores:
        if query_params.include_functions:
            functions = [
                BasicFunctionDefinition(name=function.name, description=function.description)
                for function in app.functions
            ]
            apps.append(AppBasic(name=app.name, description=app.description, functions=functions))
        else:
            apps.append(AppBasic(name=app.name, description=app.description))

    logger.info(
        "Search apps result",
        extra={
            "search_apps": {
                "query_params_json": query_params.model_dump_json(),
                "app_names": [app.name for app, _ in apps_with_scores],
            },
        },
    )

    return apps


@router.get("/{app_name}", response_model_exclude_none=True)
async def get_app_details(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    app_name: str,
) -> AppDetails:
    """
    Returns an application (name, description, and functions).
    """
    app = crud.apps.get_app(
        context.db_session,
        app_name,
        True,
    )

    if not app:
        logger.error(f"App not found, app_name={app_name}")

        raise AppNotFound(f"App={app_name} not found")


    functions = [
        function
        for function in app.functions
        if function.active
    ]

    app_details: AppDetails = AppDetails(
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
        supported_security_schemes=SecuritySchemesPublic.model_validate(app.security_schemes),
        has_default_credentials=app.has_default_credentials,
        is_configured=app.has_configuration,
        is_linked=app.has_linked_account(context.user.id),
        functions=[FunctionDetails.model_validate(function) for function in functions],
        created_at=app.created_at,
        updated_at=app.updated_at,
    )

    return app_details
