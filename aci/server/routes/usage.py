from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from aci.common.db import crud
from aci.common.db.crud import usage as usage_crud
from aci.common.schemas.usage import (
    UserUsageResponse,
    UserUsageStats,
    UserUsageHistory,
    MonthlyUsageStats,
)
from aci.server.dependencies import ErrorCode, PaymentRequiredErrorDetail, RequestContext, get_request_context

router = APIRouter()


@router.get("/current", response_model=UserUsageResponse)
def get_current_month_usage(
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
):
    """Get current subscription period usage for the authenticated user."""
    start_date = context.user.subscription_period_starts_at
    end_date = context.user.subscription_expires_at

    # If no billing window, user is unsubscribed
    if not start_date or not end_date:
        error_detail = PaymentRequiredErrorDetail(
            code=ErrorCode.SUBSCRIPTION_REQUIRED,
            message="User is not subscribed.",
        )
        raise HTTPException(
            status_code=402,
            detail=error_detail.model_dump(),
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
            year=now.year,
            month=now.month,
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


@router.get("/stats", response_model=UserUsageStats)
def get_usage_stats(
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
):
    """Get aggregated usage statistics for the authenticated user."""
    stats_data = usage_crud.get_usage_stats(context.db_session, context.user.id)

    current_month_data = stats_data["current_month"]
    current_month = MonthlyUsageStats(
        voice_minutes=current_month_data["voice_minutes"],
        automation_runs=current_month_data["automation_runs"],
        successful_runs=current_month_data["successful_runs"],
        failed_runs=current_month_data["failed_runs"],
        success_rate=(
            (
                current_month_data["successful_runs"]
                / current_month_data["automation_runs"]
            )
            * 100
            if current_month_data["automation_runs"] > 0
            else 0
        ),
        llm_tokens=current_month_data["llm_tokens"],
        stt_minutes=current_month_data["stt_minutes"],
        tts_characters=current_month_data["tts_characters"],
    )

    return UserUsageStats(
        total_voice_minutes=stats_data["total_voice_minutes"],
        total_automation_runs=stats_data["total_automation_runs"],
        total_successful_runs=stats_data["total_successful_runs"],
        total_failed_runs=stats_data["total_failed_runs"],
        total_automations_created=stats_data["total_automations_created"],
        success_rate=stats_data["success_rate"],
        total_llm_tokens=stats_data["total_llm_tokens"],
        total_stt_minutes=stats_data["total_stt_minutes"],
        total_tts_characters=stats_data["total_tts_characters"],
        current_month=current_month,
    )


@router.get("/history", response_model=UserUsageHistory)
def get_usage_history(
    limit: Optional[int] = Query(
        12, ge=1, le=24, description="Number of months to return"
    ),
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
):
    """Get usage history for the authenticated user."""
    usage_records = usage_crud.get_user_usage_history(
        context.db_session, context.user.id, limit
    )
    stats_data = usage_crud.get_usage_stats(context.db_session, context.user.id)

    current_month_data = stats_data["current_month"]
    current_month = MonthlyUsageStats(
        voice_minutes=current_month_data["voice_minutes"],
        automation_runs=current_month_data["automation_runs"],
        successful_runs=current_month_data["successful_runs"],
        failed_runs=current_month_data["failed_runs"],
        success_rate=(
            (
                current_month_data["successful_runs"]
                / current_month_data["automation_runs"]
            )
            * 100
            if current_month_data["automation_runs"] > 0
            else 0
        ),
        llm_tokens=current_month_data["llm_tokens"],
        stt_minutes=current_month_data["stt_minutes"],
        tts_characters=current_month_data["tts_characters"],
    )

    stats = UserUsageStats(
        total_voice_minutes=stats_data["total_voice_minutes"],
        total_automation_runs=stats_data["total_automation_runs"],
        total_successful_runs=stats_data["total_successful_runs"],
        total_failed_runs=stats_data["total_failed_runs"],
        total_automations_created=stats_data["total_automations_created"],
        success_rate=stats_data["success_rate"],
        total_llm_tokens=stats_data["total_llm_tokens"],
        total_stt_minutes=stats_data["total_stt_minutes"],
        total_tts_characters=stats_data["total_tts_characters"],
        current_month=current_month,
    )

    return UserUsageHistory(
        usage_records=[UserUsageResponse.from_orm(record) for record in usage_records],
        total_records=len(usage_records),
        stats=stats,
    )


@router.get("/{year}/{month}", response_model=UserUsageResponse)
def get_specific_month_usage(
    year: int,
    month: int,
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
):
    """Get usage for a specific month and year."""
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12")

    if year < 2000 or year > 3000:
        raise HTTPException(status_code=400, detail="Invalid year")

    usage = usage_crud.get_user_usage(context.db_session, context.user.id, month, year)

    if not usage:
        # Return zero usage if no record exists for that period
        now = datetime.now(timezone.utc)
        return UserUsageResponse(
            id="",
            user_id=context.user.id,
            year=year,
            month=month,
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

    return UserUsageResponse.model_validate(usage)


@router.get("/automations-count")
def get_user_automations_count(
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
):
    """Get total number of automations created by the authenticated user."""
    count = usage_crud.get_user_total_automations_count(
        context.db_session, context.user.id
    )
    return {"total_automations_created": count}
