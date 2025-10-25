from datetime import datetime, timezone
from fastapi import APIRouter, Request, status, Depends
from fastapi.responses import JSONResponse
from aci.common.db.sql_models import SupabaseUser
from aci.common.enums import SubscriptionStatus
from aci.common.logging_setup import get_logger
from aci.common.db import crud
from aci.common.schemas.webhooks import (
    RevenueCatWebhookPayload,
    WebhookResponse,
    RevenueCatEvent,
)
from aci.server import dependencies as deps
from sqlalchemy.orm import Session

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "/revenuecat",
    response_model=WebhookResponse,
    summary="Handle RevenueCat Webhook",
    description="Process RevenueCat webhook events for subscription and purchase updates.",
    dependencies=[Depends(deps.verify_revenuecat_signature)],
)
def revenuecat_webhook(
    payload: RevenueCatWebhookPayload,
    request: Request,
    context: deps.RequestContext = Depends(deps.get_request_context_no_auth),
) -> JSONResponse:
    """
    Handle RevenueCat webhook events for subscription and purchase updates.

    This endpoint is protected and will only accept requests with a valid
    RevenueCat Authorization Bearer token.

    RevenueCat sends webhooks for various events like:
    - INITIAL_PURCHASE: First purchase of a subscription
    - RENEWAL: Subscription renewal
    - CANCEL: Subscription cancellation
    - UNCANCEL: Subscription reactivation
    - EXPIRED: Subscription expiration
    - NON_RENEWING: Subscription won't renew

    The webhook payload contains detailed event information including user ID,
    product details, transaction information, and subscription status.
    """
    # Store webhook event in database for audit purposes immediately
    event_id = None
    try:
        webhook_event = crud.webhook_events.create_event(
            session=context.db_session,
            provider="revenuecat",
            event_type=payload.event.type,
            event_data=payload.model_dump(),
            user_id=payload.event.app_user_id,
            processed=False,  # Will be updated after processing
            http_status_code=None,  # Will be set based on final response
        )
        event_id = webhook_event.id
    except Exception as e:
        logger.warning(f"Failed to store webhook event: {e}")

    try:
        # The code here will only run if verify_revenuecat_signature passes
        logger.info(f"Received authenticated RevenueCat webhook: {payload.event.type}")

        # Extract event data with proper typing
        event = payload.event
        event_type: str = event.type
        app_user_id: str = event.app_user_id
        
        # Log the webhook event for debugging
        logger.info(
            f"Processing RevenueCat event: {event_type} for user: {app_user_id}"
        )

        # Handle different event types
        _process_revenuecat_event(event, context.db_session)

        # Update the stored event as processed successfully
        if event_id:
            try:
                crud.webhook_events.update_event_status(
                    session=context.db_session,
                    event_id=event_id,
                    processed=True,
                    processing_result="Successfully processed",
                    http_status_code=200,
                )
            except Exception as e:
                logger.warning(f"Failed to update webhook event status: {e}")

        # Return success response to RevenueCat
        return JSONResponse(
            content=WebhookResponse(status="success").model_dump(),
            status_code=status.HTTP_200_OK,
        )

    except Exception as e:
        logger.error(f"Error processing RevenueCat webhook: {e}", exc_info=True)

        # Update the stored event as failed
        if event_id:
            try:
                crud.webhook_events.update_event_status(
                    session=context.db_session,
                    event_id=event_id,
                    processed=False,
                    processing_result=str(e),
                    http_status_code=500,
                )
            except Exception as update_e:
                logger.warning(f"Failed to update webhook event status on error: {update_e}")

        return JSONResponse(
            content=WebhookResponse(status="error", message=str(e)).model_dump(),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _ms_to_datetime(ms: int | None) -> datetime | None:
    """Converts milliseconds timestamp to a timezone-aware datetime object."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _process_revenuecat_event(event: RevenueCatEvent, db: Session) -> None:
    """
    Process a RevenueCat event and update user subscription status accordingly.

    Args:
        event: The RevenueCat event data
        db: The SQLAlchemy database session
    """
    app_user_id = event.app_user_id

    # 1. Find the user in your database
    # We must operate on a user that already exists in our system.
    # RevenueCat should not be the source of user creation.
    user = db.query(SupabaseUser).filter(SupabaseUser.id == app_user_id).first()

    if not user:
        logger.warning(
            f"Received RevenueCat event '{event.type}' for non-existent "
            f"user ID: {app_user_id}. Ignoring."
        )
        return

    # 2. Extract key event data
    expires_at = _ms_to_datetime(event.expiration_date_ms) # Corrected field name
    product_id = event.product_id
    period_type = event.period_type
    event_type = event.type

    # 3. Handle "stale event" logic
    # For events that set an expiration date, we should only process them if
    # they are newer than the currently stored expiration date.
    is_stale = (
        user.subscription_expires_at
        and expires_at
        and expires_at < user.subscription_expires_at
    )

    # These events grant access and define an expiration date
    if event_type in ["INITIAL_PURCHASE", "RENEWAL", "UNCANCEL", "PRODUCT_CHANGE"]:
        if is_stale:
            logger.warning(
                f"Stale event '{event_type}' for user {app_user_id}. "
                f"Event expiry ({expires_at}) is before "
                f"stored expiry ({user.subscription_expires_at}). Ignoring."
            )
            return

        # This is a trial
        if period_type == "trial":
            user.subscription_status = SubscriptionStatus.TRIALING
            user.is_trial = True
            user.trial_ends_at = expires_at
        # This is a regular paid subscription
        else:
            user.subscription_status = SubscriptionStatus.ACTIVE
            user.is_trial = False
            user.trial_ends_at = None

        user.subscription_product_id = product_id
        user.subscription_expires_at = expires_at

    # User turned off auto-renew. Access is still valid until expires_at.
    elif event_type == "CANCEL":
        # Only update if the user is not already expired
        if user.subscription_status != SubscriptionStatus.EXPIRED:
            user.subscription_status = SubscriptionStatus.CANCELLED
        
    # A billing issue occurred. Access is often still valid (grace period).
    elif event_type == "BILLING_ISSUE":
        user.subscription_status = SubscriptionStatus.UNPAID

    # Access has officially ended.
    elif event_type == "EXPIRED":
        # Robustness check: Only expire if the event's expiration date
        # matches our stored expiration date. This prevents a stale
        # EXPIRED event from an old subscription from overriding a
        # brand new, active subscription.
        if (
            user.subscription_expires_at 
            and expires_at 
            and user.subscription_expires_at.date() != expires_at.date()
        ):
            logger.warning(
                f"Ignoring '{event_type}' event for user {app_user_id}. "
                f"Event expiry ({expires_at}) does not match "
                f"stored expiry ({user.subscription_expires_at})."
            )
            return

        user.subscription_status = SubscriptionStatus.EXPIRED
        user.is_trial = False # Ensure trial is off when expired

    # This event is just a notification. The "EXPIRED" event
    # is what actually revokes access. We can safely ignore this
    # or just log it.
    elif event_type == "SUBSCRIPTION_PAUSED":
        logger.info(
            f"User {app_user_id} paused subscription. "
            "No status change until EXPIRATION event."
        )

    else:
        # NON_RENEWING will now be logged here
        logger.info(f"Unhandled RevenueCat event type: {event_type}")
        
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(
            f"Successfully processed event '{event_type}' for user {app_user_id}. "
            f"New status: {user.subscription_status}, Is Trial: {user.is_trial}"
        )
    except Exception as e:
        logger.error(f"Failed to commit subscription update for user {app_user_id}: {e}")
        db.rollback()
        raise  # Re-raise the exception to be caught by the main handler