from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from aci.common.db.sql_models import AutomationRun, Automation
from aci.common.enums import RunStatus
from sqlalchemy import func


def get_user_activity(
    db: Session,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
    status_filter: Optional[RunStatus] = None
) -> List[AutomationRun]:
    """
    Get user activity feed based on automation runs.
    
    Args:
        db: Database session
        user_id: User ID to get activity for
        limit: Maximum number of activities to return
        offset: Number of activities to skip
        status_filter: Optional status filter (success, failure, in_progress, etc.)
    
    Returns:
        List of AutomationRun objects with automation relationship loaded
    """
    stmt = (
        select(AutomationRun)
        .join(Automation)
        .where(Automation.user_id == user_id)
        .order_by(desc(AutomationRun.started_at))
        .offset(offset)
        .limit(limit)
    )
    
    if status_filter:
        stmt = stmt.where(AutomationRun.status == status_filter)
    
    return list(db.execute(stmt).scalars().all())


def get_user_activity_count(
    db: Session,
    user_id: str,
    status_filter: Optional[RunStatus] = None
) -> int:
    """
    Get total count of user activities.

    Args:
        db: Database session
        user_id: User ID to count activities for
        status_filter: Optional status filter

    Returns:
        Total count of activities

    Optimized with efficient join and indexing.
    """
    from sqlalchemy import func

    stmt = (
        select(func.count(AutomationRun.id))
        .join(Automation)
        .where(Automation.user_id == user_id)
    )

    if status_filter:
        stmt = stmt.where(AutomationRun.status == status_filter)

    return db.execute(stmt).scalar() or 0


def get_recent_user_activity(
    db: Session,
    user_id: str,
    days: int = 7
) -> List[AutomationRun]:
    """
    Get user activity from the last N days.
    
    Args:
        db: Database session
        user_id: User ID to get activity for
        days: Number of days to look back
    
    Returns:
        List of AutomationRun objects from the last N days
    """
    from datetime import timedelta, timezone
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    stmt = (
        select(AutomationRun)
        .join(Automation)
        .where(Automation.user_id == user_id)
        .where(AutomationRun.started_at >= cutoff_date)
        .order_by(desc(AutomationRun.started_at))
    )
    
    return list(db.execute(stmt).scalars().all())


def get_activity_stats(db: Session, user_id: str) -> dict:
    """
    Get activity statistics for a user.
    
    Args:
        db: Database session
        user_id: User ID to get stats for
    
    Returns:
        Dictionary with activity statistics
    """    
    # Get counts by status
    stmt = (
        select(
            AutomationRun.status,
            func.count(AutomationRun.id).label("count")
        )
        .join(Automation)
        .where(Automation.user_id == user_id)
        .group_by(AutomationRun.status)
    )
    
    status_counts = {}
    for row in db.execute(stmt):
        status_counts[row.status.value] = row.count
    
    # Get recent activity (last 7 days)
    recent_activity = get_recent_user_activity(db, user_id, days=7)
    
    return {
        "total_runs": sum(status_counts.values()),
        "successful_runs": status_counts.get("success", 0),
        "failed_runs": status_counts.get("failure", 0),
        "in_progress_runs": status_counts.get("in_progress", 0),
        "recent_activity_count": len(recent_activity),
        "status_breakdown": status_counts
    }
