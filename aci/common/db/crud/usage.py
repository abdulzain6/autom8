from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from aci.common.db.sql_models import UserUsage, Automation


def get_user_usage(
    session: Session, user_id: str, month: int, year: int
) -> Optional[UserUsage]:
    """Get user usage for a specific month and year."""
    return session.query(UserUsage).filter(
        and_(
            UserUsage.user_id == user_id,
            UserUsage.month == month,
            UserUsage.year == year
        )
    ).first()


def get_or_create_user_usage(
    session: Session, user_id: str, month: int, year: int
) -> UserUsage:
    """Get or create user usage record for a specific month and year."""
    usage = get_user_usage(session, user_id, month, year)
    if not usage:
        usage = UserUsage(
            user_id=user_id,
            month=month,
            year=year,
            voice_agent_minutes=0.0,
            automation_runs_count=0,
            successful_automation_runs=0,
            failed_automation_runs=0,
            llm_tokens_used=0,
            stt_audio_minutes=0.0,
            tts_characters_used=0
        )
        session.add(usage)
        session.commit()
        session.refresh(usage)
    return usage


def increment_voice_minutes(
    session: Session, user_id: str, minutes: float, month: Optional[int] = None, year: Optional[int] = None
) -> UserUsage:
    """Increment voice minutes for a user."""
    now = datetime.now(timezone.utc)
    if month is None:
        month = now.month
    if year is None:
        year = now.year
    
    usage = get_or_create_user_usage(session, user_id, month, year)
    usage.voice_agent_minutes += minutes
    usage.updated_at = now
    session.commit()
    session.refresh(usage)
    return usage


def increment_automation_runs(
    session: Session, user_id: str, success: bool = True, month: Optional[int] = None, year: Optional[int] = None
) -> UserUsage:
    """Increment automation runs count for a user."""
    now = datetime.now(timezone.utc)
    if month is None:
        month = now.month
    if year is None:
        year = now.year
    
    usage = get_or_create_user_usage(session, user_id, month, year)
    usage.automation_runs_count += 1
    if success:
        usage.successful_automation_runs += 1
    else:
        usage.failed_automation_runs += 1
    usage.updated_at = now
    session.commit()
    session.refresh(usage)
    return usage


def increment_llm_tokens(
    session: Session, user_id: str, tokens: int, month: Optional[int] = None, year: Optional[int] = None
) -> UserUsage:
    """Increment LLM tokens used for a user."""
    now = datetime.now(timezone.utc)
    if month is None:
        month = now.month
    if year is None:
        year = now.year
    
    usage = get_or_create_user_usage(session, user_id, month, year)
    usage.llm_tokens_used += tokens
    usage.updated_at = now
    session.commit()
    session.refresh(usage)
    return usage


def increment_stt_minutes(
    session: Session, user_id: str, minutes: float, month: Optional[int] = None, year: Optional[int] = None
) -> UserUsage:
    """Increment STT audio minutes for a user."""
    now = datetime.now(timezone.utc)
    if month is None:
        month = now.month
    if year is None:
        year = now.year
    
    usage = get_or_create_user_usage(session, user_id, month, year)
    usage.stt_audio_minutes += minutes
    usage.updated_at = now
    session.commit()
    session.refresh(usage)
    return usage


def increment_tts_characters(
    session: Session, user_id: str, characters: int, month: Optional[int] = None, year: Optional[int] = None
) -> UserUsage:
    """Increment TTS characters used for a user."""
    now = datetime.now(timezone.utc)
    if month is None:
        month = now.month
    if year is None:
        year = now.year
    
    usage = get_or_create_user_usage(session, user_id, month, year)
    usage.tts_characters_used += characters
    usage.updated_at = now
    session.commit()
    session.refresh(usage)
    return usage


def increment_usage_from_livekit_metrics(
    session: Session, user_id: str, llm_tokens: int = 0, stt_minutes: float = 0.0, 
    tts_characters: int = 0, month: Optional[int] = None, year: Optional[int] = None
) -> UserUsage:
    """Bulk increment usage metrics from LiveKit usage collector."""
    now = datetime.now(timezone.utc)
    if month is None:
        month = now.month
    if year is None:
        year = now.year
    
    usage = get_or_create_user_usage(session, user_id, month, year)
    
    if llm_tokens > 0:
        usage.llm_tokens_used += llm_tokens
    if stt_minutes > 0:
        usage.stt_audio_minutes += stt_minutes
    if tts_characters > 0:
        usage.tts_characters_used += tts_characters
    
    usage.updated_at = now
    session.commit()
    session.refresh(usage)
    return usage


def get_user_usage_history(
    session: Session, user_id: str, limit: Optional[int] = 12
) -> list[UserUsage]:
    """Get user usage history, ordered by year and month descending."""
    query = (
        session.query(UserUsage)
        .filter(UserUsage.user_id == user_id)
        .order_by(UserUsage.year.desc(), UserUsage.month.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def get_current_month_usage(session: Session, user_id: str) -> Optional[UserUsage]:
    """Get current month usage for a user."""
    now = datetime.now(timezone.utc)
    return get_user_usage(session, user_id, now.month, now.year)


def get_usage_stats(session: Session, user_id: str) -> dict:
    """Get aggregated usage statistics for a user."""
    usages = get_user_usage_history(session, user_id, limit=None)
    
    # User-facing metrics
    total_voice_minutes = sum(usage.voice_agent_minutes for usage in usages)
    total_automation_runs = sum(usage.automation_runs_count for usage in usages)
    total_successful_runs = sum(usage.successful_automation_runs for usage in usages)
    total_failed_runs = sum(usage.failed_automation_runs for usage in usages)
    
    # Internal cost metrics
    total_llm_tokens = sum(usage.llm_tokens_used for usage in usages)
    total_stt_minutes = sum(usage.stt_audio_minutes for usage in usages)
    total_tts_characters = sum(usage.tts_characters_used for usage in usages)
    
    # Get total automations created by user
    total_automations_created = get_user_total_automations_count(session, user_id)
    
    current_month = get_current_month_usage(session, user_id)
    
    return {
        # User-facing metrics
        "total_voice_minutes": total_voice_minutes,
        "total_automation_runs": total_automation_runs,
        "total_successful_runs": total_successful_runs,
        "total_failed_runs": total_failed_runs,
        "total_automations_created": total_automations_created,
        "success_rate": (total_successful_runs / total_automation_runs) * 100 if total_automation_runs > 0 else 0,
        
        # Internal cost metrics
        "total_llm_tokens": total_llm_tokens,
        "total_stt_minutes": total_stt_minutes,
        "total_tts_characters": total_tts_characters,
        
        "current_month": {
            # User-facing
            "voice_minutes": current_month.voice_agent_minutes if current_month else 0,
            "automation_runs": current_month.automation_runs_count if current_month else 0,
            "successful_runs": current_month.successful_automation_runs if current_month else 0,
            "failed_runs": current_month.failed_automation_runs if current_month else 0,
            
            # Internal cost metrics
            "llm_tokens": current_month.llm_tokens_used if current_month else 0,
            "stt_minutes": current_month.stt_audio_minutes if current_month else 0,
            "tts_characters": current_month.tts_characters_used if current_month else 0,
        }
    }


def get_user_total_automations_count(session: Session, user_id: str) -> int:
    """Get total number of automations created by a user."""
    return session.query(Automation).filter(Automation.user_id == user_id).count()
