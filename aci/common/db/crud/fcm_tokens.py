from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select
from aci.common.db.sql_models import FCMToken
from ...schemas.fcm_tokens import FCMTokenUpsert
import logging


logger = logging.getLogger(__name__)


def upsert_fcm_token(
    db: Session, *, user_id: str, token_in: FCMTokenUpsert
) -> FCMToken:
    """
    Creates or updates a user's FCM token for a specific device type,
    ensuring uniqueness for both the token and the user/device combo.
    """
    
    # 1. Find any record that ALREADY has this token
    token_record = db.execute(
        select(FCMToken).where(FCMToken.token == token_in.token)
    ).scalar_one_or_none()

    # 2. Find any record that this user ALREADY has for this device type
    device_record = db.execute(
        select(FCMToken).where(
            FCMToken.user_id == user_id,
            FCMToken.device_type == token_in.device_type
        )
    ).scalar_one_or_none()

    try:
        # Case 1: Token exists, device record exists, and they are the same row.
        # User is re-registering the same token on the same device. Nothing to do.
        if token_record and device_record and token_record.id == device_record.id:
            return token_record

        # Case 2: Token exists, but it's on a different row (or device_record is None).
        # This token is being "stolen" from another user or re-assigned from another of the
        # *current* user's devices.
        if token_record:
            # If the current user had an old token on this device, that record is now stale.
            if device_record:
                db.delete(device_record)
            
            # Re-assign the existing token record to this user and device
            token_record.user_id = user_id
            token_record.device_type = token_in.device_type
            db.commit()
            db.refresh(token_record)
            return token_record

        # Case 3: Token does not exist, but a record for this device does.
        # User is getting a new token for an existing device (e.g., app re-install).
        if device_record:
            # Update the old record with the new token
            device_record.token = token_in.token
            db.commit()
            db.refresh(device_record)
            return device_record

        # Case 4: Token does not exist, device record does not exist.
        # This is a brand new registration for this user and device.
        new_token = FCMToken(
            user_id=user_id,
            token=token_in.token,
            device_type=token_in.device_type,
        )
        db.add(new_token)
        db.commit()
        db.refresh(new_token)
        return new_token
        
    except Exception as e:
        db.rollback() # Rollback on any error
        logger.error(f"Error during FCM token upsert: {e}", exc_info=True)
        raise e


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

