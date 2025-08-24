from huey import crontab
from aci.common.enums import RunStatus
from .config import huey
from logging import getLogger
from aci.server.dependencies import get_db_session
from aci.common.db.crud import automations, automation_runs
from aci.server.agent.automation_executor import AutomationExecutor


logger = getLogger(__name__)


@huey.periodic_task(crontab(minute="*"))
def schedule_due_automations():
    """
    This is the scheduler 'beat'. It finds due automations, creates a run record
    to "lock" them, and then enqueues the execution task.
    """
    logger.info("Scheduler beat: Checking for due automations...")
    with get_db_session() as db_session:
        try:
            due_automations = automations.get_due_recurring_automations(db_session)
            if not due_automations:
                logger.info("Scheduler beat: No automations are due.")
                return

            logger.info(
                f"Scheduler beat: Found {len(due_automations)} due automation(s)."
            )
            for automation in due_automations:
                logger.info(
                    f"Scheduler beat: Creating run and enqueuing task for automation {automation.id}."
                )
                automation_run = automation_runs.create_run(db_session, automation.id)
                execute_automation(automation_run.id)

        except Exception as e:
            logger.error(f"Scheduler beat: An error occurred: {e}", exc_info=True)


@huey.task()
def execute_automation(run_id: str):
    """
    Executes a single, specific automation run that has already been created.
    """
    logger.info(f"Executing automation for run_id: {run_id}")
    with get_db_session() as db_session:
        automation_run = automation_runs.get_run(db_session, run_id)
        if not automation_run:
            logger.error(
                f"Could not execute task, AutomationRun with ID {run_id} not found."
            )
            return

        automation_id = automation_run.automation_id

        try:
            automation = automations.get_automation_with_eager_loaded_functions(
                db_session, automation_id
            )
            if not automation:
                raise ValueError(
                    f"Parent Automation with ID {automation_id} not found."
                )

            executor = AutomationExecutor(automation, run_id=run_id)
            automation_output = executor.run()

            logger.info(f"Automation Output: {automation_output}")

            automation_runs.finalize_run(
                db_session,
                run_id=automation_run.id,
                status=RunStatus[automation_output.status],
                message=automation_output.automation_output,
                artifact_ids=automation_output.artifact_ids,
            )
            logger.info(
                f"Automation {automation_id} executed successfully for run {run_id}."
            )

        except Exception as e:
            logger.error(
                f"Error executing automation {automation_id} for run {run_id}: {e}",
                exc_info=True,
            )
            automation_runs.finalize_run(
                db_session,
                run_id=run_id,
                status=RunStatus.failure,
                message=f"An unexpected error occurred: {e}",
            )
