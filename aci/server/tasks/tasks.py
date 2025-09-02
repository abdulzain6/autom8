from huey import crontab
from aci.common.enums import RunStatus
from .config import huey
from logging import getLogger
from aci.server.dependencies import get_db_session
from aci.common.db.crud import automations, automation_runs
from aci.server.agent.automation_executor import AutomationExecutor
from aci.common.fcm import FCMManager


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
                return # No commit needed as no changes were made

            logger.info(
                f"Scheduler beat: Found {len(due_automations)} due automation(s)."
            )
            for automation in due_automations:
                logger.info(
                    f"Scheduler beat: Creating run and enqueuing task for automation {automation.id}."
                )
                automation_run = automation_runs.create_run(db_session, automation.id)
                execute_automation(automation_run.id)

            db_session.commit()

        except Exception as e:
            logger.error(f"Scheduler beat: An error occurred: {e}", exc_info=True)
            db_session.rollback()


@huey.task()
def execute_automation(run_id: str):
    """
    Executes a single, specific automation run that has already been created.
    """
    logger.info(f"Executing automation for run_id: {run_id}")
    
    # Initialize FCM manager
    fcm_manager = FCMManager()
    
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

            # Send notification that automation is starting
            try:
                fcm_manager.send_notification_to_user(
                    db=db_session,
                    user_id=automation.user_id,
                    title="🚀 Automation Started",
                    body=f"'{automation.name}' is now running...",
                    data={
                        "type": "automation_started",
                        "automation_id": automation_id,
                        "run_id": run_id,
                        "automation_name": automation.name
                    }
                )
                logger.info(f"Sent start notification for automation {automation.name} to user {automation.user_id}")
            except Exception as e:
                logger.warning(f"Failed to send start notification: {e}")

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
            
            # Send success notification
            try:
                fcm_manager.send_notification_to_user(
                    db=db_session,
                    user_id=automation.user_id,
                    title="✅ Automation Completed",
                    body=f"'{automation.name}' finished successfully!",
                    data={
                        "type": "automation_completed",
                        "automation_id": automation_id,
                        "run_id": run_id,
                        "automation_name": automation.name,
                        "status": automation_output.status,
                        "message": automation_output.automation_output[:100] if automation_output.automation_output else ""
                    }
                )
                logger.info(f"Sent success notification for automation {automation.name} to user {automation.user_id}")
            except Exception as e:
                logger.warning(f"Failed to send success notification: {e}")
            
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
            
            # Send failure notification
            try:
                automation = automations.get_automation(db_session, automation_id)
                if automation:
                    fcm_manager.send_notification_to_user(
                        db=db_session,
                        user_id=automation.user_id,
                        title="❌ Automation Failed",
                        body=f"'{automation.name}' encountered an error",
                        data={
                            "type": "automation_failed",
                            "automation_id": automation_id,
                            "run_id": run_id,
                            "automation_name": automation.name,
                            "error_message": str(e)[:100]
                        }
                    )
                    logger.info(f"Sent failure notification for automation {automation.name} to user {automation.user_id}")
            except Exception as notification_error:
                logger.warning(f"Failed to send failure notification: {notification_error}")
