from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status

from aci.common.db import crud
from aci.common.logging_setup import get_logger
from aci.common.schemas.automation_runs import (
    AutomationRunPublic,
    AutomationRunListParams,
)
from aci.server import dependencies as deps

logger = get_logger(__name__)
router = APIRouter()


# Helper function to get and verify run ownership
def get_run_and_verify_ownership(
    run_id: str, context: deps.RequestContext
) -> crud.automations.AutomationRun:
    """Fetches a run and raises HTTP exceptions if not found or user doesn't have access."""
    run = crud.automation_runs.get_run(db=context.db_session, run_id=run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automation run not found."
        )
    if run.automation.user_id != context.user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this automation run.",
        )
    return run


@router.get(
    "/",
    response_model=List[AutomationRunPublic],
)
def list_runs_for_a_specific_automation(
    automation_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    params: Annotated[AutomationRunListParams, Depends()],
):
    """
    List all runs for a specific automation owned by the user.
    """
    # First, verify the user owns the parent automation
    automation = crud.automations.get_automation(
        db=context.db_session, automation_id=automation_id
    )
    if not automation or automation.user_id != context.user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found."
        )

    runs = crud.automation_runs.list_runs_for_automation(
        db=context.db_session,
        automation_id=automation_id,
        limit=params.limit,
        offset=params.offset,
        status=params.status,
    )
    return runs


@router.get("/{run_id}", response_model=AutomationRunPublic)
def get_a_specific_run(
    run_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
):
    """
    Retrieve a specific automation run by its ID.
    """
    run = get_run_and_verify_ownership(run_id=run_id, context=context)
    return run


@router.delete(
    "/{run_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_a_run(
    run_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
):
    """
    Delete a specific automation run.
    """
    # First, ensure the run exists and the user has permission to delete it.
    get_run_and_verify_ownership(run_id=run_id, context=context)

    # Now, call the dedicated CRUD function to perform the deletion.
    crud.automation_runs.delete_run(db=context.db_session, run_id=run_id)

    return None
