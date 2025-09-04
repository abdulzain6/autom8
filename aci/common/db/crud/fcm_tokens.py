from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select
from aci.common.db.sql_models import FCMToken
from ...schemas.fcm_tokens import FCMTokenUpsert


def upsert_fcm_token(
    db: Session, *, user_id: str, token_in: FCMTokenUpsert
) -> FCMToken:
    """
    Creates or updates a user's FCM token for a specific device type.

    This "upsert" logic ensures a user has only one token per device type.
    - If a token for the user/device_type combo exists, it updates the token value.
    - If it doesn't exist, it creates a new record.

    Args:
        db: The SQLAlchemy database session.
        user_id: The ID of the user owning the token.
        token_in: The Pydantic schema containing the new token and device type.

    Returns:
        The created or updated FCMToken object.
    """
    # Find an existing token for this user and device type.
    stmt = select(FCMToken).where(
        FCMToken.user_id == user_id,
        FCMToken.device_type == token_in.device_type
    )
    existing_token_for_device = db.execute(stmt).scalar_one_or_none()

    if existing_token_for_device:
        # If one exists, update its token value if it's different.
        if existing_token_for_device.token != token_in.token:
            existing_token_for_device.token = token_in.token
            db.commit()
        
        db.refresh(existing_token_for_device)
        return existing_token_for_device
    else:
        # If no token exists for this device type, create a new one.
        # First, ensure this token isn't somehow registered to another user,
        # which would violate the unique constraint on the token itself.
        existing_token_any_user = db.execute(
            select(FCMToken).where(FCMToken.token == token_in.token)
        ).scalar_one_or_none()

        if existing_token_any_user:
            db.delete(existing_token_any_user)
            db.commit()
            
        new_token = FCMToken(
            user_id=user_id,
            token=token_in.token,
            device_type=token_in.device_type,
        )
        db.add(new_token)
        db.commit()
        db.refresh(new_token)
        return new_token


def get_tokens_for_user(db: Session, *, user_id: str) -> List[FCMToken]:
    """Retrieves all FCM tokens associated with a specific user."""
    stmt = select(FCMToken).where(FCMToken.user_id == user_id)
    return list(db.execute(stmt).scalars().all())


def get_token_by_id_and_user(
    db: Session, *, token_id: str, user_id: str
) -> Optional[FCMToken]:
    """Retrieves a single FCM token by its ID, ensuring it belongs to the specified user."""
    stmt = select(FCMToken).where(FCMToken.id == token_id, FCMToken.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def delete_token(db: Session, *, token: FCMToken) -> None:
    """Deletes an FCMToken object from the database."""
    db.delete(token)
    db.commit()

