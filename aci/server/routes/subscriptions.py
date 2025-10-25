import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, ValidationError

from aci.common.db.sql_models import SupabaseUser
from aci.common.enums import SubscriptionStatus
from aci.common.logging_setup import get_logger
from aci.server import config
from aci.server import dependencies as deps
from aci.server.dependencies import RequestContext


logger = get_logger(__name__)
router = APIRouter()


# --- New Pydantic Response Model ---


class UserSubscriptionInfo(BaseModel):
    """
    Defines the subscription-related information for a user.
    """

    subscription_status: Optional[SubscriptionStatus] = None
    subscription_product_id: Optional[str] = None
    subscription_expires_at: Optional[datetime] = None
    is_trial: bool
    trial_ends_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[Dict[str, Any]])
def get_subscription_plans() -> List[Dict[str, Any]]:
    """
    Retrieve all available subscription plans and their limits.
    """
    return config.SUBSCRIPTION_PLANS


@router.get("/status", response_model=UserSubscriptionInfo)
def get_user_subscription_status(
    context: RequestContext = Depends(deps.get_request_context),
) -> UserSubscriptionInfo:
    """
    Retrieve the current user's subscription status and details.
    """
    db = context.db_session
    user_id = context.user.id

    # Get the full user object from our database
    user_db = db.query(SupabaseUser).filter(SupabaseUser.id == user_id).one_or_none()

    if not user_db:
        # This should not happen if user is authenticated
        logger.error(f"Authenticated user with ID {user_id} not found in database.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in database.",
        )

    try:
        return UserSubscriptionInfo.model_validate(user_db)
    except ValidationError as e:
        logger.error(f"Model validation error for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing user data.",
        )