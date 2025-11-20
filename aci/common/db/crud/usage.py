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


def update_automation_run_event(
    session: Session,
    usage_id: str,
    success: bool,
) -> UserUsage:
    """
    Updates an existing usage record to reflect the actual outcome of an automation run.
    """
    usage_event = session.get(UserUsage, usage_id)
    if not usage_event:
        raise ValueError(f"Usage event {usage_id} not found")
    
    if success:
        usage_event.successful_automation_runs = 1
        usage_event.failed_automation_runs = 0
    else:
        usage_event.successful_automation_runs = 0
        usage_event.failed_automation_runs = 1
    
    session.commit()
    session.refresh(usage_event)
    return usage_event


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
    
    # Optimized query: Direct selection, no subquery, no group_by
    result = session.query(
        func.sum(UserUsage.voice_agent_minutes),
        func.sum(UserUsage.automation_runs_count),
        func.sum(UserUsage.successful_automation_runs),
        func.sum(UserUsage.failed_automation_runs),
        func.sum(UserUsage.llm_tokens_used),
        func.sum(UserUsage.stt_audio_minutes),
        func.sum(UserUsage.tts_characters_used),
    ).filter(
        UserUsage.user_id == user_id,
        UserUsage.created_at >= start_date,
        UserUsage.created_at < end_date
    ).first()

    # If the query returns a row of Nones (which happens if no rows match), handle it
    if not result or result[0] is None:
        return None # Or return an empty UserUsage object depending on your preference

    return UserUsage(
        user_id=user_id,
        voice_agent_minutes=result[0] or 0.0,
        automation_runs_count=result[1] or 0,
        successful_automation_runs=result[2] or 0,
        failed_automation_runs=result[3] or 0,
        llm_tokens_used=result[4] or 0,
        stt_audio_minutes=result[5] or 0.0,
        tts_characters_used=result[6] or 0,
    )