from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import select

from aci.common.db.sql_models import (
    AutomationRun,
    Artifact,
    RunStatus,
)
from . import automations


def get_run(db: Session, run_id: str) -> Optional[AutomationRun]:
    """Retrieves a single automation run by its ID."""
    return db.get(AutomationRun, run_id)


def list_runs_for_automation(
    db: Session,
    automation_id: str,
    limit: int,
    offset: int,
    status: Optional[RunStatus] = None,
) -> List[AutomationRun]:
    """
    Lists all runs for a given automation with filtering and pagination.
    """
    stmt = select(AutomationRun).where(AutomationRun.automation_id == automation_id)

    if status:
        stmt = stmt.where(AutomationRun.status == status)

    stmt = stmt.order_by(AutomationRun.started_at.desc()).offset(offset).limit(limit)

    return list(db.execute(stmt).scalars().all())


def create_run(db: Session, automation_id: str) -> AutomationRun:
    """
    Creates a new run for an automation and updates the automation's last_run status.
    """
    automation = automations.get_automation(db, automation_id)
    if not automation:
        raise ValueError(f"Automation {automation_id} not found")

    start_time = datetime.now(timezone.utc)
    run = AutomationRun(
        automation_id=automation_id,
        started_at=start_time,
        status=RunStatus.in_progress,
        message=""
    )
    db.add(run)

    automation.last_run_status = RunStatus.in_progress
    automation.last_run_at = start_time

    db.flush()
    db.refresh(run)
    db.refresh(automation)
    return run


def finalize_run(
    db: Session,
    run_id: str,
    status: RunStatus,
    message: str,
    logs: Optional[dict] = None,
    artifact_ids: Optional[List[str]] = None,
) -> AutomationRun:
    """
    Finalizes a run with a status, message, logs, and a list of new artifacts to append.
    """
    run = get_run(db, run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    # If artifact IDs are provided, validate and associate them.
    if artifact_ids:
        existing_artifact_ids = {artifact.id for artifact in run.artifacts}
        new_artifact_ids = [
            aid for aid in artifact_ids if aid not in existing_artifact_ids
        ]
        # ---------------------------------------------------------

        if new_artifact_ids:
            artifacts_stmt = select(Artifact).where(Artifact.id.in_(new_artifact_ids))
            new_artifacts = list(db.execute(artifacts_stmt).scalars().all())

            found_ids = {artifact.id for artifact in new_artifacts}
            missing_ids = set(new_artifact_ids) - found_ids
            if missing_ids:
                raise ValueError(f"Artifacts not found: {list(missing_ids)}")

            for artifact in new_artifacts:
                if artifact.user_id != run.automation.user_id:
                    raise ValueError(
                        f"Artifact {artifact.id} does not belong to the automation owner"
                    )
            
            # Append the new, validated artifacts to the existing collection.
            run.artifacts.extend(new_artifacts)

    finish_time = datetime.now(timezone.utc)
    run.status = status
    run.message = message # <-- Set the final message
    run.finished_at = finish_time
    if logs is not None:
        run.logs = logs

    if run.automation:
        run.automation.last_run_status = status
        run.automation.last_run_at = finish_time

    db.flush()
    db.refresh(run)
    if run.automation:
        db.refresh(run.automation)
    return run


def delete_run(db: Session, run_id: str) -> None:
    """Deletes an automation run from the database."""
    run = get_run(db, run_id)
    if run:
        db.delete(run)
        db.commit()
