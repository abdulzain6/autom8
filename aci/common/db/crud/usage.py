from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from aci.common.db.sql_models import UserUsage, Automation


def create_automation_run_event(
    session: Session,
    user_id: str,
    success: bool = True,
) -> UserUsage:
    """
    Creates a new usage record for a single automation run.
    This corresponds to the "one record per usage" model.
    """
    new_usage_event = UserUsage(
        user_id=user_id,
        automation_runs_count=1,
        successful_automation_runs=1 if success else 0,
        failed_automation_runs=0 if success else 1,
        # All other metrics default to 0
        voice_agent_minutes=0.0,
        llm_tokens_used=0,
        stt_audio_minutes=0.0,
        tts_characters_used=0,
    )
    session.add(new_usage_event)
    session.commit()
    session.refresh(new_usage_event)
    return new_usage_event


def create_voice_session_event(
    session: Session,
    user_id: str,
    voice_agent_minutes: float,
    llm_tokens: int = 0,
    stt_minutes: float = 0.0,
    tts_characters: int = 0,
) -> UserUsage:
    """
    Creates a new usage record for a completed voice agent session.
    This corresponds to the "one record per usage" model and logs
    all relevant metrics from the session.
    """
    new_usage_event = UserUsage(
        user_id=user_id,
        voice_agent_minutes=voice_agent_minutes,
        llm_tokens_used=llm_tokens,
        stt_audio_minutes=stt_minutes,
        tts_characters_used=tts_characters,
        # All other metrics default to 0
        automation_runs_count=0,
        successful_automation_runs=0,
        failed_automation_runs=0,
    )
    session.add(new_usage_event)
    session.commit()
    session.refresh(new_usage_event)
    return new_usage_event


def get_user_total_automations_count(session: Session, user_id: str) -> int:
    """Get total number of automations created by a user."""
    return session.query(Automation).filter(Automation.user_id == user_id).count()


def get_usage_between_dates(
    session: Session, user_id: str, start_date: datetime, end_date: datetime
) -> Optional[UserUsage]:
    """
    Gets aggregated user usage for a specific date range by filtering
    the 'created_at' timestamp (for the transactional usage model).

    Returns a single, unmanaged UserUsage-like object with summed values.
    """

    # Create a filter for rows where created_at is between the
    # start and end of the billing period.
    date_filter = and_(
        UserUsage.created_at >= start_date, UserUsage.created_at < end_date
    )

    # We create a subquery to aggregate the sums for all metrics
    usage_agg = (
        session.query(
            UserUsage.user_id,
            func.sum(UserUsage.voice_agent_minutes).label("voice_agent_minutes"),
            func.sum(UserUsage.automation_runs_count).label("automation_runs_count"),
            func.sum(UserUsage.successful_automation_runs).label(
                "successful_automation_runs"
            ),
            func.sum(UserUsage.failed_automation_runs).label("failed_automation_runs"),
            func.sum(UserUsage.llm_tokens_used).label("llm_tokens_used"),
            func.sum(UserUsage.stt_audio_minutes).label("stt_audio_minutes"),
            func.sum(UserUsage.tts_characters_used).label("tts_characters_used"),
        )
        .filter(UserUsage.user_id == user_id, date_filter)  # Apply filters
        .group_by(UserUsage.user_id)
        .subquery()
    )

    # Query from the aggregated subquery
    result = session.query(
        usage_agg.c.user_id,
        usage_agg.c.voice_agent_minutes,
        usage_agg.c.automation_runs_count,
        usage_agg.c.successful_automation_runs,
        usage_agg.c.failed_automation_runs,
        usage_agg.c.llm_tokens_used,
        usage_agg.c.stt_audio_minutes,
        usage_agg.c.tts_characters_used,
    ).first()

    if not result:
        return None

    # Return an unmanaged UserUsage object with the summed data.
    # We use or 0 / or 0.0 because func.sum() returns None for no rows.
    return UserUsage(
        user_id=result.user_id,
        voice_agent_minutes=result.voice_agent_minutes or 0.0,
        automation_runs_count=result.automation_runs_count or 0,
        successful_automation_runs=result.successful_automation_runs or 0,
        failed_automation_runs=result.failed_automation_runs or 0,
        llm_tokens_used=result.llm_tokens_used or 0,
        stt_audio_minutes=result.stt_audio_minutes or 0.0,
        tts_characters_used=result.tts_characters_used or 0,
    )