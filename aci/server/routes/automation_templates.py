from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status

from aci.common.db import crud
from aci.common.logging_setup import get_logger
from aci.common.schemas.automation_templates import (
    AutomationTemplatePublic,
    AutomationTemplateListParams,
)
from aci.server import dependencies as deps

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "/",
    response_model=List[AutomationTemplatePublic],
)
def list_all_templates(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    params: Annotated[AutomationTemplateListParams, Depends()],
):
    """
    Retrieve a list of all available automation templates.
    """
    templates = crud.automation_templates.list_templates(
        db=context.db_session, limit=params.limit, offset=params.offset
    )
    return templates


@router.get(
    "/{template_id}",
    response_model=AutomationTemplatePublic,
)
def get_template_by_id(
    template_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
):
    """
    Retrieve a specific automation template by its ID.
    """
    template = crud.automation_templates.get_template(
        db=context.db_session, template_id=template_id
    )
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found."
        )
    return template
