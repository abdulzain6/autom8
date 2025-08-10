from sqlalchemy.orm import Session
from typing import Optional

from aci.common.db.sql_models import UserProfile
from aci.common.schemas.profiles import UserProfileUpdate


def get_profile(db: Session, user_id: str) -> Optional[UserProfile]:
    """
    Retrieves a user's profile by their user ID.

    Args:
        db: The SQLAlchemy database session.
        user_id: The ID of the user.

    Returns:
        The UserProfile object if found, otherwise None.
    """
    return db.query(UserProfile).filter(UserProfile.id == user_id).first()


def create_profile(
    db: Session, user_id: str, profile_in: UserProfileUpdate
) -> UserProfile:
    """
    Creates a new profile for a user.

    Args:
        db: The SQLAlchemy database session.
        user_id: The ID of the user to create a profile for.
        profile_in: The Pydantic schema with the profile data.

    Returns:
        The newly created UserProfile object.
    """
    new_profile = UserProfile(id=user_id, **profile_in.model_dump())
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)
    return new_profile


def update_profile(
    db: Session, user_id: str, profile_in: UserProfileUpdate
) -> UserProfile:
    """
    Updates an existing user's profile. If the profile does not exist,
    it creates a new one (upsert behavior).

    Args:
        db: The SQLAlchemy database session.
        user_id: The ID of the user whose profile is being updated.
        profile_in: The Pydantic schema with the new profile data.

    Returns:
        The updated or newly created UserProfile object.
    """
    profile = get_profile(db, user_id)
    if not profile:
        # If the profile doesn't exist, create it.
        return create_profile(db, user_id, profile_in)

    # Update the existing profile with non-null values from the input schema
    update_data = profile_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(profile, key, value)

    db.commit()
    db.refresh(profile)
    return profile
