import jwt
from pydantic import BaseModel, ConfigDict
from collections.abc import Generator
from typing import Annotated, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from aci.common import utils
from aci.common.logging_setup import get_logger
from aci.server import config


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
    # This is a placeholder for your actual database session creation logic
    # For this example, we'll simulate a session.
    db_session = utils.create_db_session(config.DB_FULL_URL)
    try:
        yield db_session
    finally:
        db_session.close()


# --- Authentication and Authorization ---
def get_current_user(
    token: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)]
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