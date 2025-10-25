from fastapi import APIRouter, Request, status, Depends
from fastapi.responses import JSONResponse
from aci.common.logging_setup import get_logger
from aci.common.db import crud
from aci.common.schemas.webhooks import (
    RevenueCatWebhookPayload,
    WebhookResponse,
    RevenueCatEvent,
)
from aci.server import dependencies as deps

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "/revenuecat",
    response_model=WebhookResponse,
    summary="Handle RevenueCat Webhook",
    description="Process RevenueCat webhook events for subscription and purchase updates.",
    dependencies=[Depends(deps.verify_revenuecat_signature)],
)
async def revenuecat_webhook(
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
        await _process_revenuecat_event(event)

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


async def _process_revenuecat_event(event: RevenueCatEvent) -> None:
    """
    Process a RevenueCat event and update user subscription status accordingly.

    Args:
        event: The RevenueCat event data
    """
    event_type = event.type
    app_user_id = event.app_user_id
    product_id = event.product_id

    if event_type in ["INITIAL_PURCHASE", "RENEWAL", "UNCANCEL"]:
        # User has active subscription - you might want to update user status
        logger.info(
            f"User {app_user_id} has active subscription for product {product_id}"
        )

        # TODO: Update user subscription status in database
        # You could add a subscription status table or update user profile
        # Example:
        # crud.subscriptions.update_subscription_status(
        #     db=db_session,
        #     user_id=app_user_id,
        #     product_id=product_id,
        #     status="active",
        #     expires_at=datetime.fromtimestamp(event.expiration_date_ms / 1000) if event.expiration_date_ms else None
        # )

    elif event_type == "CANCEL":
        # User cancelled subscription
        logger.info(
            f"User {app_user_id} cancelled subscription for product {product_id}"
        )

        # TODO: Handle subscription cancellation
        # Mark user as cancelled but still active until expiration
        # crud.subscriptions.update_subscription_status(
        #     db=db_session,
        #     user_id=app_user_id,
        #     product_id=product_id,
        #     status="cancelled"
        # )

    elif event_type == "EXPIRED":
        # Subscription expired
        logger.info(f"User {app_user_id} subscription expired for product {product_id}")

        # TODO: Handle subscription expiration
        # Update user status to expired/inactive
        # crud.subscriptions.update_subscription_status(
        #     db=db_session,
        #     user_id=app_user_id,
        #     product_id=product_id,
        #     status="expired"
        # )

    elif event_type == "NON_RENEWING":
        # Subscription won't renew
        logger.info(
            f"User {app_user_id} subscription will not renew for product {product_id}"
        )

        # TODO: Handle non-renewing subscription
        # crud.subscriptions.update_subscription_status(
        #     db=db_session,
        #     user_id=app_user_id,
        #     product_id=product_id,
        #     status="non_renewing"
        # )

    else:
        logger.info(f"Unhandled RevenueCat event type: {event_type}")
