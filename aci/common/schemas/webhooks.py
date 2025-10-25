from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class RevenueCatEvent(BaseModel):
    """
    A comprehensive model for the 'event' object in a RevenueCat webhook.
    Fields are based on the official documentation.
    """

    # --- Core Event Info ---
    id: str = Field(..., description="Unique ID for this webhook event")
    type: str = Field(..., description="The type of event (e.g., INITIAL_PURCHASE, RENEWAL, CANCEL)")
    event_timestamp_ms: int = Field(..., description="Timestamp of when the event occurred, in ms")

    # --- User IDs ---
    app_user_id: str = Field(..., description="The user ID from your app")
    original_app_user_id: str = Field(..., description="The original app_user_id this user was merged into, if any")
    aliases: List[str] = Field(..., description="List of app_user_id aliases associated with this user")

    # --- Product & Transaction ---
    product_id: str = Field(..., description="The product ID of the subscription")
    entitlement_ids: Optional[List[str]] = Field(default=None, description="List of granted entitlement IDs")
    transaction_id: str = Field(..., description="The store's transaction ID")
    original_transaction_id: str = Field(..., description="The original transaction ID for the subscription")
    store: str = Field(..., description="The app store (e.g., APP_STORE, PLAY_STORE, STRIPE)")
    environment: str = Field(..., description="SANDBOX or PRODUCTION")

    # --- Subscription Period & Status ---
    period_type: str = Field(..., description="Type of period (e.g., NORMAL, INTRO, TRIAL)")
    purchase_date_ms: int = Field(..., description="Purchase date in milliseconds")
    expiration_date_ms: Optional[int] = Field(default=None, description="Expiration date in milliseconds")
    grace_period_expiration_date_ms: Optional[int] = Field(default=None, description="Grace period expiration date, if any")
    is_trial_conversion: Optional[bool] = Field(default=None, description="True if this event is a trial conversion")

    # --- Price & Currency ---
    price: Optional[float] = Field(default=None, description="Price of the purchase in USD")
    price_in_purchased_currency: Optional[float] = Field(default=None, description="Price in the original purchase currency")
    currency: Optional[str] = Field(default=None, description="Currency code (e.g., USD)")
    takehome_percentage: Optional[float] = Field(default=None, description="The percentage of revenue you take home")

    # --- Event-Specific Fields ---
    cancel_reason: Optional[str] = Field(default=None, description="Reason for cancellation (for CANCEL events)")
    unsubscribe_detected_at_ms: Optional[int] = Field(default=None, description="When an unsubscribe was detected")
    billing_issues_detected_at_ms: Optional[int] = Field(default=None, description="When a billing issue was detected")
    
    # --- Transfer Fields (for TRANSFER events) ---
    transferred_from: Optional[List[str]] = Field(default=None, description="List of app_user_ids transferred from")
    transferred_to: Optional[List[str]] = Field(default=None, description="List of app_user_ids transferred to")


class RevenueCatWebhookPayload(BaseModel):
    """
    RevenueCat webhook payload model.
    This wraps the event and includes the API version.
    """

    event: RevenueCatEvent = Field(..., description="The event data")
    api_version: str = Field(..., description="API version (e.g., '1.0')")


class WebhookResponse(BaseModel):
    """Standard webhook response model."""

    status: str = Field(..., description="Status of the webhook processing")
    message: Optional[str] = Field(default=None, description="Optional message")