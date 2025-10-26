from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_

from aci.common.db.sql_models import UserUsage, Automation


def get_user_usage(
    session: Session, user_id: str, month: int, year: int
) -> Optional[UserUsage]:
    """Get user usage for a specific month and year."""
    return (
        session.query(UserUsage)
        .filter(
            and_(
                UserUsage.user_id == user_id,
                UserUsage.month == month,
                UserUsage.year == year,
            )
        )
        .first()
    )


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
            tts_characters_used=0,
        )
        session.add(usage)
        session.commit()
        session.refresh(usage)
    return usage


def increment_voice_minutes(
    session: Session,
    user_id: str,
    minutes: float,
    month: Optional[int] = None,
    year: Optional[int] = None,
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
    session: Session,
    user_id: str,
    success: bool = True,
    month: Optional[int] = None,
    year: Optional[int] = None,
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
    session: Session,
    user_id: str,
    tokens: int,
    month: Optional[int] = None,
    year: Optional[int] = None,
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
    session: Session,
    user_id: str,
    minutes: float,
    month: Optional[int] = None,
    year: Optional[int] = None,
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
    session: Session,
    user_id: str,
    characters: int,
    month: Optional[int] = None,
    year: Optional[int] = None,
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
    session: Session,
    user_id: str,
    llm_tokens: int = 0,
    stt_minutes: float = 0.0,
    tts_characters: int = 0,
    month: Optional[int] = None,
    year: Optional[int] = None,
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
        "success_rate": (
            (total_successful_runs / total_automation_runs) * 100
            if total_automation_runs > 0
            else 0
        ),
        # Internal cost metrics
        "total_llm_tokens": total_llm_tokens,
        "total_stt_minutes": total_stt_minutes,
        "total_tts_characters": total_tts_characters,
        "current_month": {
            # User-facing
            "voice_minutes": current_month.voice_agent_minutes if current_month else 0,
            "automation_runs": (
                current_month.automation_runs_count if current_month else 0
            ),
            "successful_runs": (
                current_month.successful_automation_runs if current_month else 0
            ),
            "failed_runs": current_month.failed_automation_runs if current_month else 0,
            # Internal cost metrics
            "llm_tokens": current_month.llm_tokens_used if current_month else 0,
            "stt_minutes": current_month.stt_audio_minutes if current_month else 0,
            "tts_characters": current_month.tts_characters_used if current_month else 0,
        },
    }


def get_user_total_automations_count(session: Session, user_id: str) -> int:
    """Get total number of automations created by a user."""
    return session.query(Automation).filter(Automation.user_id == user_id).count()


def get_usage_between_dates(
    session: Session, user_id: str, start_date: datetime, end_date: datetime
) -> Optional[UserUsage]:
    """
    Gets aggregated user usage for a specific date range.
    Returns a single UserUsage-like object with summed values.

    This query correctly handles ranges spanning across years.
    Example: Dec 2024 (12, 2024) to Feb 2025 (2, 2025)
    """
    start_year = start_date.year
    start_month = start_date.month
    end_year = end_date.year
    end_month = end_date.month

    # This logic constructs a query like:
    # WHERE user_id = :user_id AND (
    #   (year > :start_year AND year < :end_year) OR -- Full years in between
    #   (year = :start_year AND month >= :start_month) OR -- Months in the start year
    #   (year = :end_year AND month <= :end_month)      -- Months in the end year
    # )

    # Handle the case where start and end year are the same
    if start_year == end_year:
        date_filter = and_(
            UserUsage.year == start_year,
            UserUsage.month >= start_month,
            UserUsage.month <= end_month,
        )
    # Handle ranges across different years
    else:
        date_filter = and_(
            UserUsage.user_id == user_id,  # Ensure user_id is part of the filter
            or_(
                # (year = 2024 AND month >= 12)
                (UserUsage.year == start_year) & (UserUsage.month >= start_month),
                # (year = 2025 AND month <= 2)
                (UserUsage.year == end_year) & (UserUsage.month <= end_month),
                # (year > 2024 AND year < 2025) -- (handles multi-year spans)
                (UserUsage.year > start_year) & (UserUsage.year < end_year),
            ),
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
        year=end_year,  # Use end date for reference
        month=end_month,
        voice_agent_minutes=result.voice_agent_minutes or 0.0,
        automation_runs_count=result.automation_runs_count or 0,
        successful_automation_runs=result.successful_automation_runs or 0,
        failed_automation_runs=result.failed_automation_runs or 0,
        llm_tokens_used=result.llm_tokens_used or 0,
        stt_audio_minutes=result.stt_audio_minutes or 0.0,
        tts_characters_used=result.tts_characters_used or 0,
    )


def get_current_year_usage(session: Session, user_id: str) -> Optional[UserUsage]:
    """
    Gets aggregated user usage for the current calendar year.
    Returns a single UserUsage-like object with summed values.
    """
    now = datetime.now(timezone.utc)

    # We create a subquery to aggregate the sums
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
        .filter(UserUsage.user_id == user_id, UserUsage.year == now.year)
        .group_by(UserUsage.user_id)
        .subquery()
    )

    # We query from the subquery to make it easy to map to a UserUsage object
    # This returns None if there is no usage this year
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

    # Manually create a UserUsage object with the summed data.
    # It's "unmanaged" (not in the session), but has the data we need.
    return UserUsage(
        user_id=result.user_id,
        year=now.year,
        month=now.month,  # month/year are just for reference
        voice_agent_minutes=result.voice_agent_minutes or 0.0,
        automation_runs_count=result.automation_runs_count or 0,
        successful_automation_runs=result.successful_automation_runs or 0,
        failed_automation_runs=result.failed_automation_runs or 0,
        llm_tokens_used=result.llm_tokens_used or 0,
        stt_audio_minutes=result.stt_audio_minutes or 0.0,
        tts_characters_used=result.tts_characters_used or 0,
    )
