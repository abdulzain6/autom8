from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status
from aci.common.db import crud
from aci.common.db.crud.automations import _validate_and_fetch_linked_accounts
from aci.common.logging_setup import get_logger
from aci.common.schemas.automations import (
    AutomationCreate,
    AutomationFromTemplateCreate,
    AutomationPublic,
    AutomationRunResponse,
    AutomationUpdate,
    AutomationListParams,
)
from aci.server import dependencies as deps
from aci.server.tasks.tasks import execute_automation
from aci.common.utils import generate_automation_description


logger = get_logger(__name__)
router = APIRouter()


@router.post("", response_model=AutomationPublic, status_code=status.HTTP_201_CREATED)
def create_new_automation(
    automation_in: AutomationCreate,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
    _limit_check: bool = Depends(deps.UsageLimiter(deps.LimitType.AUTOMATIONS_TOTAL))
):
    """
    Create a new automation for the authenticated user.
    Automatically generates a description using LLM if none is provided.
    """
    try:
        if not automation_in.description:
            linked_accounts = _validate_and_fetch_linked_accounts(
                db=context.db_session, 
                user_id=context.user.id, 
                linked_account_ids=automation_in.linked_account_ids
            )
            automation_in.description = generate_automation_description(
                name=automation_in.name,
                goal=automation_in.goal,
                app_names=[la.app_name for la in linked_accounts]
            )
            logger.info(f"Generated description for new automation: {automation_in.description}")
        new_automation = crud.automations.create_automation(
            db=context.db_session,
            user_id=context.user.id,
            automation_in=automation_in,
        )
        return new_automation
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("", response_model=List[AutomationPublic])
def list_my_automations(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
    params: Annotated[AutomationListParams, Depends()],
):
    """
    Retrieve all automations owned by the authenticated user.
    """
    automations = crud.automations.list_user_automations(
        db=context.db_session,
        user_id=context.user.id,
        limit=params.limit,
        offset=params.offset,
    )
    return automations


@router.get("/{automation_id}", response_model=AutomationPublic)
def get_automation_by_id(
    automation_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
):
    """
    Retrieve a specific automation by its ID.
    """
    automation = crud.automations.get_automation(
        db=context.db_session, automation_id=automation_id
    )

    if not automation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found."
        )

    if automation.user_id != context.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this automation.",
        )

    return automation


@router.put("/{automation_id}", response_model=AutomationPublic)
def update_existing_automation(
    automation_id: str,
    automation_in: AutomationUpdate,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
):
    """
    Update an existing automation.
    """
    # First, verify the automation exists and belongs to the user
    db_automation = crud.automations.get_automation(
        db=context.db_session, automation_id=automation_id
    )
    if not db_automation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found."
        )
    if db_automation.user_id != context.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this automation.",
        )

    try:
        updated_automation = crud.automations.update_automation(
            db=context.db_session,
            automation_id=automation_id,
            automation_in=automation_in,
        )
        return updated_automation
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_automation(
    automation_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
):
    """
    Delete an automation by its ID.
    """
    # Verify the automation exists and belongs to the user before attempting to delete
    db_automation = crud.automations.get_automation(
        db=context.db_session, automation_id=automation_id
    )
    if not db_automation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found."
        )
    if db_automation.user_id != context.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to delete this automation.",
        )

    crud.automations.delete_automation(
        db=context.db_session, automation_id=automation_id
    )
    return None



@router.post(
    "/from-template",
    response_model=AutomationPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_automation_from_a_template(
    template_data: AutomationFromTemplateCreate,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
    _limit_check: bool = Depends(deps.UsageLimiter(deps.LimitType.AUTOMATIONS_TOTAL))
):
    """
    Create a new automation by rendering a template with the provided variables.
    """
    try:
        new_automation = crud.automations.create_automation_from_template(
            db=context.db_session,
            user_id=context.user.id,
            template_data=template_data,
        )
        return new_automation
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{automation_id}/run",
    response_model=AutomationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_an_automation(
    automation_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context())],
    _limit_check: bool = Depends(deps.UsageLimiter(deps.LimitType.AUTOMATION_RUNS))
):
    """
    Manually triggers a run for a specific automation.
    """
    automation = crud.automations.get_automation(
        db=context.db_session, automation_id=automation_id
    )
    if not automation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found."
        )
    if automation.user_id != context.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to run this automation.",
        )
    
    # Create the run record first to get a run_id and "lock" the automation
    automation_run = crud.automation_runs.create_run(context.db_session, automation_id)
    
    context.db_session.commit()
    
    # Enqueue the task with the specific run_id
    execute_automation(automation_run.id)
    
    return {
        "message": "Automation run has been successfully queued.",
        "run_id": automation_run.id,
    }