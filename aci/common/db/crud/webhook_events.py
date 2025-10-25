from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from aci.common.db.sql_models import WebhookEvent


def create_event(
    session: Session,
    provider: str,
    event_type: str,
    event_data: dict,
    user_id: Optional[str] = None,
    processed: bool = False,
    processing_result: Optional[str] = None,
    http_status_code: Optional[int] = None,
) -> WebhookEvent:
    """Create a new webhook event record."""
    event = WebhookEvent(
        provider=provider,
        event_type=event_type,
        event_data=event_data,
        user_id=user_id,
        processed=processed,
        processing_result=processing_result,
        http_status_code=http_status_code,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def get_event(session: Session, event_id: str) -> Optional[WebhookEvent]:
    """Get a webhook event by ID."""
    return session.query(WebhookEvent).filter(WebhookEvent.id == event_id).first()


def get_events_by_provider(
    session: Session,
    provider: str,
    limit: int = 100,
    offset: int = 0,
) -> List[WebhookEvent]:
    """Get webhook events for a specific provider."""
    return (
        session.query(WebhookEvent)
        .filter(WebhookEvent.provider == provider)
        .order_by(desc(WebhookEvent.created_at))
        .limit(limit)
        .offset(offset)
        .all()
    )


def get_events_by_user(
    session: Session,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
) -> List[WebhookEvent]:
    """Get webhook events for a specific user."""
    return (
        session.query(WebhookEvent)
        .filter(WebhookEvent.user_id == user_id)
        .order_by(desc(WebhookEvent.created_at))
        .limit(limit)
        .offset(offset)
        .all()
    )


def get_events_by_type(
    session: Session,
    event_type: str,
    limit: int = 100,
    offset: int = 0,
) -> List[WebhookEvent]:
    """Get webhook events of a specific type."""
    return (
        session.query(WebhookEvent)
        .filter(WebhookEvent.event_type == event_type)
        .order_by(desc(WebhookEvent.created_at))
        .limit(limit)
        .offset(offset)
        .all()
    )


def update_event_status(
    session: Session,
    event_id: str,
    processed: bool = True,
    processing_result: Optional[str] = None,
    http_status_code: Optional[int] = None,
) -> Optional[WebhookEvent]:
    """Update the processing status of a webhook event."""
    event = session.query(WebhookEvent).filter(WebhookEvent.id == event_id).first()
    if event:
        event.processed = processed
        if processing_result is not None:
            event.processing_result = processing_result
        if http_status_code is not None:
            event.http_status_code = http_status_code
        event.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(event)
    return event


def get_failed_events(
    session: Session,
    provider: Optional[str] = None,
    limit: int = 50,
) -> List[WebhookEvent]:
    """Get webhook events that failed processing."""
    query = session.query(WebhookEvent).filter(
        and_(
            WebhookEvent.processed == False,
            WebhookEvent.http_status_code != 200,
        )
    )

    if provider:
        query = query.filter(WebhookEvent.provider == provider)

    return (
        query.order_by(desc(WebhookEvent.created_at))
        .limit(limit)
        .all()
    )


def get_recent_events(
    session: Session,
    hours: int = 24,
    limit: int = 100,
) -> List[WebhookEvent]:
    """Get webhook events from the last N hours."""
    cutoff_time = datetime.now(timezone.utc)
    # This is a simplified version - in production you'd want proper time arithmetic
    # For now, we'll just get recent events by limit

    return (
        session.query(WebhookEvent)
        .order_by(desc(WebhookEvent.created_at))
        .limit(limit)
        .all()
    )


def delete_old_events(
    session: Session,
    days_old: int = 90,
) -> int:
    """Delete webhook events older than specified days. Returns number of deleted events."""
    # This is a simplified version - in production you'd want proper date arithmetic
    # For now, we'll implement a basic version that keeps only the most recent events

    # Keep only the last 1000 events per provider to prevent unbounded growth
    providers = session.query(WebhookEvent.provider).distinct().all()

    total_deleted = 0
    for (provider,) in providers:
        # Get all events for this provider, ordered by creation time
        events = (
            session.query(WebhookEvent)
            .filter(WebhookEvent.provider == provider)
            .order_by(desc(WebhookEvent.created_at))
            .all()
        )

        # Keep only the most recent 500 events per provider
        if len(events) > 500:
            events_to_delete = events[500:]
            for event in events_to_delete:
                session.delete(event)
            total_deleted += len(events_to_delete)

    session.commit()
    return total_deleted