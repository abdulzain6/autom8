from typing import Annotated, List, Set
from fastapi import APIRouter, Depends, HTTPException, status

from aci.common.db import crud
from aci.common.enums import SecurityScheme
from aci.common.logging_setup import get_logger
from aci.common.schemas.automation_templates import (
    AppForTemplatePublic,
    AutomationTemplatePublic,
    AutomationTemplateListParams,
)
from aci.server import dependencies as deps

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "/categories",
    response_model=List[str],
)
@deps.typed_cache(expire=350) 
def get_all_template_categories(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    Retrieve a list of all unique categories (tags) used in automation templates.
    """
    categories = crud.automation_templates.get_all_categories(db=context.db_session)
    return categories


@router.get(
    "/",
    response_model=List[AutomationTemplatePublic],
)
@deps.typed_cache(expire=350) 
def list_all_templates(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
    params: Annotated[AutomationTemplateListParams, Depends()],
):
    """
    Retrieve a list of all available automation templates, with user-specific
    information on which required apps are linked.

    When category is specified, pagination is applied within that category only.
    When no category is specified, pagination is applied globally across all templates.
    """
    # 1. Fetch the base templates with category-aware pagination
    templates = crud.automation_templates.list_templates(
        db=context.db_session,
        limit=params.limit,
        offset=params.offset,
        category=params.category,
        search_query=params.search_query,
    )

    # 2. Gather all unique required app IDs from the templates
    all_required_app_ids: Set[str] = {
        app.id for template in templates for app in template.required_apps
    }

    # 3. Fetch all of the user's linked accounts for those apps in a single query
    user_linked_accounts = crud.linked_accounts.get_linked_accounts_for_apps(
        db=context.db_session,
        user_id=context.user.id,
        app_ids=list(all_required_app_ids),
    )
    linked_app_ids: Set[str] = {la.app_id for la in user_linked_accounts}

    # 4. Build the final response DTOs
    response: List[AutomationTemplatePublic] = []
    for template in templates:
        # Double-check category filtering at the API level
        # This ensures no templates from other categories leak through
        if params.category and params.category not in template.tags:
            continue

        required_apps_with_status = []
        template_app_ids = set()

        for app in template.required_apps:
            is_linked = app.id in linked_app_ids
            required_apps_with_status.append(
                AppForTemplatePublic(
                    id=app.id,
                    name=app.name,
                    display_name=app.display_name,
                    logo=app.logo,
                    is_linked=is_linked,
                    security_scheme=list(app.security_schemes.keys()),
                    instructions=app.security_schemes.get(
                        SecurityScheme.API_KEY, {}
                    ).get("instructions", None),
                    linked_account_id=next(
                        (la.id for la in user_linked_accounts if la.app_id == app.id),
                        None,
                    ),
                )
            )
            template_app_ids.add(app.id)

        # Determine if all apps for this specific template are linked
        all_linked = template_app_ids.issubset(linked_app_ids)

        response.append(
            AutomationTemplatePublic(
                id=template.id,
                name=template.name,
                description=template.description,
                tags=template.tags,
                goal=template.goal,
                is_deep=template.is_deep,
                variable_names=template.variable_names,
                required_apps=required_apps_with_status,
                all_apps_linked=all_linked,
                banner_image_url=template.banner_image_url,
            )
        )

    return response


@router.get(
    "/{template_id}",
    response_model=AutomationTemplatePublic,
)
@deps.typed_cache(expire=350) 
def get_template_by_id(
    template_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    Retrieve a specific automation template by its ID, with user-specific
    information on which required apps are linked.
    """
    template = crud.automation_templates.get_template(
        db=context.db_session, template_id=template_id
    )
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found."
        )

    required_app_ids = [app.id for app in template.required_apps]

    user_linked_accounts = crud.linked_accounts.get_linked_accounts_for_apps(
        db=context.db_session, user_id=context.user.id, app_ids=required_app_ids
    )
    linked_app_ids: Set[str] = {la.app_id for la in user_linked_accounts}

    required_apps_with_status = [
        AppForTemplatePublic(
            id=app.id,
            name=app.name,
            display_name=app.display_name,
            logo=app.logo,
            is_linked=app.id in linked_app_ids,
            security_scheme=list(app.security_schemes.keys()),
            instructions=app.security_schemes.get(SecurityScheme.API_KEY, {}).get(
                "instructions", None
            ),
            linked_account_id=next(
                (la.id for la in user_linked_accounts if la.app_id == app.id), None
            ),
        )
        for app in template.required_apps
    ]

    all_linked = set(required_app_ids).issubset(linked_app_ids)

    return AutomationTemplatePublic(
        id=template.id,
        name=template.name,
        description=template.description,
        tags=template.tags,
        goal=template.goal,
        is_deep=template.is_deep,
        variable_names=template.variable_names,
        required_apps=required_apps_with_status,
        all_apps_linked=all_linked,
        banner_image_url=template.banner_image_url
    )
