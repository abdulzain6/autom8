from contextlib import contextmanager
from pydantic import BaseModel, ConfigDict
from collections.abc import Generator
from typing import Annotated, Awaitable, Optional, TypeVar, overload
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import text
from aci.common import utils
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
    It automatically commits on success, rolls back on error,
    and ensures the session is always closed.
    Enhanced for long-running operations with robust error handling.
    """
    db_session = None
    try:
        db_session = utils.create_db_session(config.DB_FULL_URL)
        
        # Verify the connection is in a good state before proceeding
        try:
            # Test the connection with a simple query
            db_session.execute(text("SELECT 1"))
        except Exception as connection_test_error:
            logger.warning(f"Database connection test failed, creating new session: {connection_test_error}")
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
                if hasattr(db_session, 'in_transaction') and db_session.in_transaction():
                    db_session.rollback()
                elif db_session.is_active:
                    # Try to rollback if the session is active
                    db_session.rollback()
            except Exception as rollback_error:
                logger.error(f"Error during rollback: {rollback_error}")
                
                # Handle specific psycopg/SQLAlchemy transaction errors
                error_str = str(e).lower()
                rollback_error_str = str(rollback_error).lower()
                
                if any(keyword in error_str or keyword in rollback_error_str for keyword in [
                    'pending rollback', 'invalid transaction', 'intrans', 'autocommit',
                    'connection in transaction status', 'programmingerror'
                ]):
                    logger.warning("Database transaction state error detected, forcing connection cleanup")
                    try:
                        # Force invalidate the connection to clear transaction state
                        if hasattr(db_session, 'connection'):
                            db_session.connection().invalidate()
                        
                        # Close the session completely
                        db_session.close()
                        
                        # Create a fresh session for cleanup if needed
                        db_session = None
                        
                        # For specific autocommit/transaction errors, don't re-raise after cleanup
                        if any(keyword in error_str for keyword in ['autocommit', 'intrans', 'pending rollback']):
                            logger.info("Handled database transaction state error, connection cleaned up")
                            return
                            
                    except Exception as cleanup_error:
                        logger.error(f"Error during connection cleanup: {cleanup_error}")
        
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
                    if hasattr(db_session, 'connection'):
                        db_session.connection().invalidate()
                except Exception as invalidate_error:
                    logger.error(f"Error invalidating connection during cleanup: {invalidate_error}")


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


# --- Request Context ---
def get_request_context(
    db_session: Annotated[Session, Depends(yield_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RequestContext:
    """
    Returns a RequestContext object containing the DB session and the authenticated user.
    """
    return RequestContext(
        db_session=db_session,
        user=current_user,
    )

def typed_cache(*, expire: int | None = None) -> Callable[..., Any]:
    """
    A type-safe wrapper around fastapi_cache.decorator.cache that supports
    both sync and async functions without causing type errors.
    """
    return cache(expire=expire)
