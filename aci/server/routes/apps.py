from typing import Annotated, List
from fastapi import APIRouter, Depends, Query
from gotrue import Field
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
    AutomationTemplateBasic,
)
from aci.common.schemas.function import BasicFunctionDefinition, FunctionDetails
from aci.common.schemas.security_scheme import SecuritySchemesPublic
from aci.server import config
from aci.server import dependencies as deps


logger = get_logger(__name__)
router = APIRouter()
openai_client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)



@router.get("/categories", response_model_exclude_none=True)
@deps.typed_cache(expire=3600)  # Cache for 1 hour since categories change infrequently
def get_all_categories(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
) -> list[str]:
    """
    Get all unique categories (tags) from all active apps.
    Returns a sorted list of unique category names that can be used for filtering.
    """
    categories = crud.apps.get_all_unique_categories(
        db_session=context.db_session,
        active_only=True,
        configured_only=True
    )
    
    logger.info(
        f"Retrieved {len(categories)} unique categories",
        extra={
            "categories": categories,
            "total_categories": len(categories)
        }
    )
    
    return categories

@router.get("", response_model_exclude_none=True)
@deps.typed_cache(expire=350) 
def list_apps(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
    query_params: Annotated[AppsList, Depends()],
    app_names: Annotated[list[str] | None, Query()] = None,
) -> list[AppDetails]:
    """
    Get a list of Apps and their details. Sorted by primary category, then by app name.
    """
    results = crud.apps.list_apps_with_user_context(
        db=context.db_session,
        user_id=context.user.id,
        active_only=True,
        configured_only=True,
        app_names=app_names,
        limit=query_params.limit,
        offset=query_params.offset,
        return_automation_templates=query_params.return_automation_templates,
    )

    response: list[AppDetails] = []
    for app, linked_account, templates in results:
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
            ] if query_params.return_functions else None,
            created_at=app.created_at,
            updated_at=app.updated_at,
            is_configured=app.has_configuration,
            is_linked=linked_account is not None,
            linked_account_id=linked_account.id if linked_account else None,
            instructions=app.security_schemes.get(SecurityScheme.API_KEY, {}).get(
                "instructions", None
            ),
            related_automation_templates=[
                AutomationTemplateBasic.model_validate(t, from_attributes=True) for t in templates
            ]
        )
        response.append(app_details)

    return response


@router.get("/search", response_model_exclude_none=True)
def search_apps(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
    query_params: Annotated[AppsSearch, Depends()],
    categories: Annotated[list[str] | None, Query()] = None,
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

    results = crud.apps.search_apps(
        db_session=context.db_session,
        user_id=context.user.id,
        active_only=True,
        configured_only=True,
        app_names=None,
        categories=categories,
        intent_embedding=intent_embedding,
        limit=query_params.limit,
        offset=query_params.offset,
        return_automation_templates=query_params.return_automation_templates,
    )

    response: list[AppBasic] = []
    for app, linked_account, _, templates in results:
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
            "related_automation_templates": [
                AutomationTemplateBasic.model_validate(t, from_attributes=True) for t in templates
            ]
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
                "app_names": [app.name for app, _, __, ___ in results],
            },
        },
    )
    return response


@router.get("/{app_name}", response_model_exclude_none=True)
@deps.typed_cache(expire=350) 
def get_app_details(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
    app_name: str,
    include_functions: bool = Query(
        True, description="Whether to include function details in the response."
    ),
) -> AppDetails:
    """
    Returns an application's details, including related automation templates.
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

    app, linked_account, related_templates = result

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
        related_automation_templates=[
            AutomationTemplateBasic.model_validate(template, from_attributes=True)
            for template in related_templates
        ],
    )
    return app_details
