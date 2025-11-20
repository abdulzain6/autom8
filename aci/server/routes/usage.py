from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from aci.common.db.crud import usage as usage_crud
from aci.common.schemas.usage import (
    UserUsageResponse,
)
from aci.server.dependencies import RequestContext, get_request_context
    
from aci.common.utils import get_logger


logger = get_logger(__name__)

router = APIRouter()


@router.get("/current", response_model=UserUsageResponse)
def get_current_usage(
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
):
    """Get current subscription period usage for the authenticated user."""
    logger.info
    (f"Fetching current usage for user_id={context.user.id}")
    start_date = context.user.subscription_period_starts_at
    end_date = context.user.subscription_expires_at

    # If no billing window, user is unsubscribed
    if not start_date or not end_date:
        raise HTTPException(
            status_code=402,
            detail="User is not subscribed.",
        )

    # Get usage between subscription dates
    current_usage_stats = usage_crud.get_usage_between_dates(
        context.db_session, context.user.id, start_date, end_date
    )

    if current_usage_stats:
        return UserUsageResponse.model_validate(current_usage_stats)
    else:
        # Return zero usage if no usage found in the period
        now = datetime.now(timezone.utc)
        return UserUsageResponse(
            id="",
            user_id=context.user.id,
            voice_agent_minutes=0.0,
            automation_runs_count=0,
            successful_automation_runs=0,
            failed_automation_runs=0,
            llm_tokens_used=0,
            stt_audio_minutes=0.0,
            tts_characters_used=0,
            created_at=now,
            updated_at=now,
        )


@router.get("/automations-count")
def get_user_automations_count(
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
):
    """Get total number of automations created by the authenticated user."""
    count = usage_crud.get_user_total_automations_count(
        context.db_session, context.user.id
    )
    return {"total_automations_created": count}
