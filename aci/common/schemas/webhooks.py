from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class RevenueCatEvent(BaseModel):
    # Fields your processing code uses
    app_user_id: str
    event_timestamp_ms: int
    type: str
    product_id: Optional[str] = None
    period_type: Optional[str] = None

    # --- FIX 1 ---
    # Fixes "purchase_date_ms: Field required" error.
    # The payload sends "purchased_at_ms".
    purchased_at_ms: int

    # --- FIX 2 ---
    # Fixes "transaction_id: Input should be a valid string" error.
    # The test event sends null, so it must be Optional.
    transaction_id: Optional[str] = None

    # --- FIX 3 ---
    # Fixes "original_transaction_id: Input should be a valid string" error.
    original_transaction_id: Optional[str] = None

    # --- FIX 4 (Proactive) ---
    # Your processing code uses "expiration_date_ms", but this test
    # payload sends "expiration_at_ms". We use an alias to
    # make the payload field "expiration_at_ms" map to your
    # code's variable "expiration_date_ms".
    expiration_date_ms: Optional[int] = Field(None, alias="expiration_at_ms")

    # Other common fields from payload
    environment: str
    entitlement_ids: Optional[List[str]] = None

    # Allows the model to ignore extra fields from RevenueCat
    # without crashing (e.g., "aliases", "currency", etc.)
    model_config = ConfigDict(extra="ignore")


class RevenueCatWebhookPayload(BaseModel):
    event: RevenueCatEvent
    api_version: str

    # Allow extra top-level fields
    model_config = ConfigDict(extra="ignore")


class WebhookResponse(BaseModel):
    """Standard webhook response model."""

    status: str = Field(..., description="Status of the webhook processing")
    message: Optional[str] = Field(default=None, description="Optional message")
