from huey import crontab
from aci.common.db import crud
from aci.common.db.sql_models import SupabaseUser
from aci.common.enums import RunStatus
from .config import huey
from logging import getLogger
from aci.server.dependencies import _get_user_limits, get_db_session
from aci.common.db.crud import automations, automation_runs
from aci.common.db.crud.usage import increment_automation_runs
from aci.server.agent.automation_executor import AutomationExecutor
from aci.common.fcm import FCMManager


logger = getLogger(__name__)


@huey.periodic_task(crontab(minute="*"))
def schedule_due_automations():
    """
    This is the scheduler 'beat'. It finds due automations, checks usage limits,
    creates a run record to "lock" them, and then enqueues the execution task.
    """
    logger.info("Scheduler beat: Checking for due automations...")

    try:
        with get_db_session() as db_session:
            try:
                due_automations = automations.get_due_recurring_automations(db_session)
                if not due_automations:
                    logger.info("Scheduler beat: No automations are due.")
                    return  # No commit needed

                logger.info(
                    f"Scheduler beat: Found {len(due_automations)} due automation(s)."
                )

                for automation in due_automations:
                    try:
                        # 1. Get the user for this automation
                        user = (
                            db_session.query(SupabaseUser)
                            .filter(SupabaseUser.id == automation.user_id)
                            .first()
                        )
                        if not user:
                            logger.error(
                                f"Scheduler: Automation {automation.id} has no valid user {automation.user_id}. Skipping."
                            )
                            continue

                        # 2. Get user's limits and interval from our shared dependency logic
                        limits, interval = _get_user_limits(user)
                        limit_value = limits.get("max_automation_runs", 0)

                        # 3. Get user's current usage based on their billing interval
                        current_usage = 0
                        current_usage_stats = None
                        if interval == "month":
                            current_usage_stats = crud.usage.get_current_month_usage(
                                db_session, user.id
                            )
                        elif interval == "year":
                            # Use the billing-period-aware function
                            if (
                                user.subscription_period_starts_at
                                and user.subscription_expires_at
                            ):
                                current_usage_stats = (
                                    crud.usage.get_usage_between_dates(
                                        db_session,
                                        user.id,
                                        user.subscription_period_starts_at,
                                        user.subscription_expires_at,
                                    )
                                )
                            else:
                                current_usage_stats = crud.usage.get_current_year_usage(
                                    db_session, user.id
                                )

                        if current_usage_stats:
                            current_usage = current_usage_stats.automation_runs_count

                        # 4. Enforce the limit
                        if current_usage >= limit_value:
                            logger.warning(
                                f"Scheduler: User {user.id} exceeded automation run limit "
                                f"({current_usage}/{limit_value}). Deactivating automation {automation.id}."
                            )
                            # Deactivate the automation
                            automation.active = False
                            db_session.add(automation)
                            db_session.commit()  # Commit deactivation
                            continue  # Skip to the next automation

                        # --- END OF USAGE LIMIT CHECK ---

                        logger.info(
                            f"Scheduler beat: Creating run and enqueuing task for automation {automation.id}."
                        )
                        automation_run = automation_runs.create_run(
                            db_session, automation.id
                        )

                        # Commit this individual run creation immediately
                        db_session.commit()

                        # Enqueue the execution task
                        execute_automation(automation_run.id)

                    except Exception as automation_error:
                        logger.error(
                            f"Scheduler beat: Failed to process automation {automation.id}: {automation_error}",
                            exc_info=True,
                        )
                        # Rollback only this automation's changes
                        db_session.rollback()
                        continue

            except Exception as e:
                logger.error(
                    f"Scheduler beat: Database error occurred: {e}", exc_info=True
                )
                db_session.rollback()

    except Exception as e:
        logger.error(
            f"Scheduler beat: Critical error - failed to establish database connection: {e}",
            exc_info=True,
        )


@huey.task()
def execute_automation(run_id: str):
    """
    Executes a single, specific automation run that has already been created.
    """
    logger.info(f"Executing automation for run_id: {run_id}")

    # Initialize FCM manager
    fcm_manager = FCMManager()

    try:
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

                # Check if automation_output is None
                if automation_output is None:
                    logger.warning(
                        f"Automation executor returned None for automation {automation_id}"
                    )
                    automation_runs.finalize_run(
                        db_session,
                        run_id=automation_run.id,
                        status=RunStatus.failure,
                        message="Automation executor returned no output",
                        artifact_ids=[],
                    )
                    # Track failed automation run in usage
                    try:
                        increment_automation_runs(
                            db_session, automation.user_id, success=False
                        )
                        logger.info(
                            f"Tracked failed automation run for user {automation.user_id}"
                        )
                    except Exception as usage_e:
                        logger.warning(f"Failed to track automation usage: {usage_e}")
                    return

                automation_runs.finalize_run(
                    db_session,
                    run_id=automation_run.id,
                    status=RunStatus[automation_output.status],
                    message=automation_output.automation_output,
                    artifact_ids=automation_output.artifact_ids,
                )

                # Track successful automation run in usage
                try:
                    increment_automation_runs(
                        db_session, automation.user_id, success=True
                    )
                    logger.info(
                        f"Tracked successful automation run for user {automation.user_id}"
                    )
                except Exception as usage_e:
                    logger.warning(f"Failed to track automation usage: {usage_e}")

                # Send success notification
                # Check if any linked account has "NOTIFYME" app
                has_notifyme = any(
                    linked_acc.linked_account.app_name == "NOTIFYME"
                    for linked_acc in automation.linked_accounts
                )

                if not has_notifyme:
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
                                "status": (
                                    automation_output.status
                                    if automation_output
                                    else "success"
                                ),
                                "message": (
                                    automation_output.automation_output[:100]
                                    if automation_output
                                    and automation_output.automation_output
                                    else ""
                                ),
                            },
                        )
                        logger.info(
                            f"Sent success notification for automation {automation.name} to user {automation.user_id}"
                        )
                    except Exception as notif_e:
                        logger.warning(
                            f"Failed to send success notification: {notif_e}"
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

                # Track failed automation run in usage
                try:
                    automation = automations.get_automation(db_session, automation_id)
                    if automation:
                        increment_automation_runs(
                            db_session, automation.user_id, success=False
                        )
                        logger.info(
                            f"Tracked failed automation run for user {automation.user_id}"
                        )
                except Exception as usage_error:
                    logger.warning(f"Failed to track automation usage: {usage_error}")

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
                                "error_message": str(e)[:100],
                            },
                        )
                        logger.info(
                            f"Sent failure notification for automation {automation.name} to user {automation.user_id}"
                        )
                except Exception as notification_error:
                    logger.warning(
                        f"Failed to send failure notification: {notification_error}"
                    )

    except Exception as critical_e:
        logger.error(
            f"Critical error in execute_automation for run_id {run_id}: {critical_e}",
            exc_info=True,
        )
