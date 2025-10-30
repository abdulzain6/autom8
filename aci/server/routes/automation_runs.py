from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from aci.common.db import crud
from aci.common.logging_setup import get_logger
from aci.common.schemas.automation_runs import (
    AutomationRunPublic,
    AutomationRunListParams,
)
from aci.server import dependencies as deps
from aci.server.file_management import FileManager

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
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
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
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    Retrieve a specific automation run by its ID.
    """
    run = get_run_and_verify_ownership(run_id=run_id, context=context)
    return run


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_a_run(
    run_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    Delete a specific automation run and its associated artifact files from storage.
    """
    run = get_run_and_verify_ownership(run_id=run_id, context=context)
    if run.artifacts:
        file_manager = FileManager(context.db_session)
        logger.info(f"Deleting {len(run.artifacts)} artifact files for run {run_id}.")
        for artifact in run.artifacts:
            try:
                file_manager.delete_from_storage("artifacts", artifact.file_path)
            except Exception as e:
                logger.error(
                    f"Failed to delete artifact file {artifact.file_path} "
                    f"from storage for run {run_id}: {e}"
                )

    crud.automation_runs.delete_run(db=context.db_session, run_id=run_id)
    return None


@router.get(
    "/runs/{run_id}/artifacts/{artifact_id}/download",
    response_description="The requested artifact file.",
)
def download_an_artifact_from_a_run(
    run_id: str,
    artifact_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    Downloads a specific artifact file associated with a specific automation run.
    """
    # 1. Verify the user owns the run
    run = get_run_and_verify_ownership(run_id=run_id, context=context)

    # 2. Verify the artifact belongs to this specific run to prevent unauthorized access
    artifact = next((art for art in run.artifacts if art.id == artifact_id), None)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact with ID {artifact_id} not found in run {run_id}.",
        )

    # 3. Use the FileManager to stream the file
    try:
        file_manager = FileManager(context.db_session)
        content_generator, mime_type = file_manager.read_artifact(artifact_id, user_id=context.user.id)

        # 4. Set headers to prompt a download with the original filename
        headers = {"Content-Disposition": f'attachment; filename="{artifact.filename}"'}

        return StreamingResponse(
            content=content_generator, media_type=mime_type, headers=headers
        )
    except ValueError as e:
        # This catches if the file is not found or expired in the FileManager
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to download artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not process file download.",
        )
