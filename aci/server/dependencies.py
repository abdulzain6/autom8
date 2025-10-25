from contextlib import contextmanager
from pydantic import BaseModel, ConfigDict
from collections.abc import Generator
from typing import Annotated, Optional
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import text
from aci.common import utils
from aci.common.db.sql_models import SupabaseUser
from aci.common.enums import SubscriptionStatus
from aci.common.logging_setup import get_logger
from aci.server import config
from typing import Callable, Any
from fastapi_cache.decorator import cache
import jwt


logger = get_logger(__name__)
http_bearer = HTTPBearer(auto_error=True, description="login to receive a JWT token")


# --- Pydantic Models ---
class User(BaseModel):
    """
    Represents the user data extracted from the Supabase JWT.
    """

    id: str
    email: Optional[str] = None
    app_metadata: Optional[dict] = None
    user_metadata: Optional[dict] = None


# --- NEW: Structured Error Models ---
class ErrorCode:
    """
    Custom error codes for 402 Payment Required errors.
    """

    SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"
    USAGE_LIMIT_EXCEEDED = "USAGE_LIMIT_EXCEEDED"


class PaymentRequiredErrorDetail(BaseModel):
    """
    Schema for the 402 error response detail.
    """

    code: str
    message: str


# -----------------------------------


class RequestContext(BaseModel):
    db_session: Session
    user: User

    model_config = ConfigDict(arbitrary_types_allowed=True)


# --- Database Session ---
def yield_db_session() -> Generator[Session, None, None]:
    """
    Yields a new database session for each request.
    """
    db_session = utils.create_db_session(config.DB_FULL_URL)
    try:
        yield db_session
    finally:
        db_session.close()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    A context manager for providing a SQLAlchemy database session.
    (Your existing robust session manager code...)
    """
    db_session = None
    try:
        db_session = utils.create_db_session(config.DB_FULL_URL)

        # Verify the connection is in a good state before proceeding
        try:
            # Test the connection with a simple query
            db_session.execute(text("SELECT 1"))
        except Exception as connection_test_error:
            logger.warning(
                f"Database connection test failed, creating new session: {connection_test_error}"
            )
            try:
                db_session.close()
            except:
                pass
            db_session = utils.create_db_session(config.DB_FULL_URL)

        yield db_session

        # Only commit if there are pending changes and no active transaction issues
        try:
            if db_session.dirty or db_session.new or db_session.deleted:
                db_session.commit()
        except Exception as commit_error:
            logger.error(f"Error during commit: {commit_error}")
            raise

    except Exception as e:
        logger.error(f"Database session error: {e}")

        if db_session is not None:
            try:
                # Check if we're in a transaction state that can be rolled back
                if (
                    hasattr(db_session, "in_transaction")
                    and db_session.in_transaction()
                ):
                    db_session.rollback()
                elif db_session.is_active:
                    # Try to rollback if the session is active
                    db_session.rollback()
            except Exception as rollback_error:
                logger.error(f"Error during rollback: {rollback_error}")

                # Handle specific psycopg/SQLAlchemy transaction errors
                error_str = str(e).lower()
                rollback_error_str = str(rollback_error).lower()

                if any(
                    keyword in error_str or keyword in rollback_error_str
                    for keyword in [
                        "pending rollback",
                        "invalid transaction",
                        "intrans",
                        "autocommit",
                        "connection in transaction status",
                        "programmingerror",
                    ]
                ):
                    logger.warning(
                        "Database transaction state error detected, forcing connection cleanup"
                    )
                    try:
                        # Force invalidate the connection to clear transaction state
                        if hasattr(db_session, "connection"):
                            db_session.connection().invalidate()

                        # Close the session completely
                        db_session.close()

                        # Create a fresh session for cleanup if needed
                        db_session = None

                        # For specific autocommit/transaction errors, don't re-raise after cleanup
                        if any(
                            keyword in error_str
                            for keyword in ["autocommit", "intrans", "pending rollback"]
                        ):
                            logger.info(
                                "Handled database transaction state error, connection cleaned up"
                            )
                            return

                    except Exception as cleanup_error:
                        logger.error(
                            f"Error during connection cleanup: {cleanup_error}"
                        )

        raise

    finally:
        if db_session is not None:
            try:
                # Ensure session is properly closed
                if db_session.is_active:
                    db_session.close()
            except Exception as close_error:
                logger.error(f"Error closing database session: {close_error}")
                # If normal close fails, try to invalidate the connection
                try:
                    if hasattr(db_session, "connection"):
                        db_session.connection().invalidate()
                except Exception as invalidate_error:
                    logger.error(
                        f"Error invalidating connection during cleanup: {invalidate_error}"
                    )


# --- Authentication and Authorization ---
def get_current_user(
    token: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)],
) -> User:
    """
    Decodes the Supabase JWT token and returns the user data.
    """
    try:
        payload = jwt.decode(
            token.credentials,
            config.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        # The 'sub' claim in a Supabase JWT corresponds to the user's ID (uid)
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return User(
            id=user_id,
            email=payload.get("email"),
            app_metadata=payload.get("app_metadata"),
            user_metadata=payload.get("user_metadata"),
        )
    except jwt.PyJWTError as e:
        logger.error(f"JWT decoding error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _get_request_context_internal(
    user: Annotated[User, Depends(get_current_user)],
    db_session: Annotated[Session, Depends(yield_db_session)],
) -> RequestContext:
    return RequestContext(db_session=db_session, user=user)


def _get_subscribed_context_internal(
    user: Annotated[User, Depends(get_current_user)],
    db_session: Annotated[Session, Depends(yield_db_session)],
) -> RequestContext:
    """
    Returns a RequestContext, but first verifies the user has an active
    subscription (ACTIVE or TRIALING).
    If not, raises 402 Payment Required.
    """
    # 1. Get the full user object from our database
    db_user = (
        db_session.query(SupabaseUser).filter(SupabaseUser.id == user.id).one_or_none()
    )

    if not db_user:
        # This should not happen if user is authenticated, implies DB is out of sync
        logger.error(f"Authenticated user with ID {user.id} not found in database.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in database.",
        )

    # 2. Check subscription status
    active_statuses = {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}

    if db_user.subscription_status not in active_statuses or not db_user.subscription_status:
        logger.warning(
            f"User {user.id} denied access for premium feature. Status: {db_user.subscription_status}"
        )

        # 3. Raise 402 with structured error
        error_detail = PaymentRequiredErrorDetail(
            code=ErrorCode.SUBSCRIPTION_REQUIRED,
            message="This feature requires an active subscription or trial.",
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=error_detail.model_dump(),
        )
    else:
        logger.info(f"User {user.id} has active subscription. Access granted.")
        logger.info(f"User subscription status: {db_user.subscription_status}")

    # 4. Return the context (with the JWT User model, as per your RequestContext schema)
    return RequestContext(db_session=db_session, user=user)


def get_request_context_no_auth(
    db_session: Annotated[Session, Depends(yield_db_session)],
) -> RequestContext:
    """
    Returns a RequestContext object containing the DB session without user authentication.
    Used for endpoints that don't require user authentication (like webhooks).
    """
    # Create a dummy user for the context - this won't be used for authorization
    dummy_user = User(id="webhook-system")
    return RequestContext(
        db_session=db_session,
        user=dummy_user,
    )


# --- Caching ---
def typed_cache(*, expire: int | None = None) -> Callable[..., Any]:
    """
    A type-safe wrapper around fastapi_cache.decorator.cache that supports
    both sync and async functions without causing type errors.
    """
    return cache(expire=expire)


# --- Webhook Verification ---
async def verify_revenuecat_signature(
    authorization: Optional[str] = Header(None),
) -> bool:
    """
    Dependency to verify the RevenueCat webhook's Authorization header.
    """
    if not config.REVENUECAT_WEBHOOK_AUTH_TOKEN:
        # This is a server configuration error, not a client error.
        logger.error("REVENUECAT_WEBHOOK_AUTH_TOKEN is not set on the server.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook authentication is not configured.",
        )

    if authorization is None:
        logger.warning("Webhook received without Authorization header.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )

    try:
        # The header should be in the format: "Bearer <your_token>"
        scheme, token = authorization.split(" ")
        if scheme.lower() != "bearer":
            raise ValueError("Invalid authorization scheme")

        if token != config.REVENUECAT_WEBHOOK_AUTH_TOKEN:
            raise ValueError("Invalid token")

    except ValueError as e:
        logger.warning(f"Webhook failed authentication: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid credentials.",
        )

    # If we get here, the token is valid
    return True


def get_request_context(
    check_subscription: bool = False
) -> Callable[..., RequestContext]:
    """
    A dependency factory that returns the correct context getter.

    :param check_subscription: If True, returns a dependency that 
                               validates the user's subscription.
    """
    if check_subscription:
        logger.info("Using subscribed request context dependency.")
        return _get_subscribed_context_internal
    else:
        logger.info("Using standard request context dependency.")
        return _get_request_context_internal