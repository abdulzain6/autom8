from typing import Optional
from aci.common.db.sql_models import UserProfile
from aci.common.schemas.profiles import UserProfileCreate, UserProfileUpdate
from sqlalchemy.orm import Session


def get_user_profile(db_session: Session, user_id: str) -> Optional[UserProfile]:
    """
    Retrieve a user profile by their user ID.

    Args:
        db_session: The database session.
        user_id: The UUID of the user.

    Returns:
        The UserProfile object or None if not found.
    """
    return db_session.get(UserProfile, user_id)


def create_user_profile(
    db_session: Session, user_id: str, profile_in: UserProfileCreate
) -> UserProfile:
    """
    Create a new user profile.
    This assumes the associated user in 'auth.users' already exists.

    Args:
        db_session: The database session.
        user_id: The UUID of the user to associate the profile with.
        profile_in: The Pydantic schema with the profile data.

    Returns:
        The newly created UserProfile object.
    """
    # Create a new UserProfile object, linking it to the user's ID
    new_profile = UserProfile(
        name=profile_in.name,
        avatar_url=profile_in.avatar_url
    )
    db_session.add(new_profile)
    db_session.commit()
    db_session.refresh(new_profile)
    return new_profile


def update_user_profile(
    db_session: Session, user_profile: UserProfile, profile_in: UserProfileUpdate
) -> UserProfile:
    """
    Update an existing user profile.

    Args:
        db_session: The database session.
        user_profile: The existing UserProfile ORM object to update.
        profile_in: A Pydantic schema with the fields to update.

    Returns:
        The updated UserProfile object.
    """
    # Get a dictionary of the fields that were actually set in the update request
    update_data = profile_in.model_dump(exclude_unset=True)
    
    # Update the model's attributes with the new data
    for key, value in update_data.items():
        setattr(user_profile, key, value)

    db_session.add(user_profile)
    db_session.commit()
    db_session.refresh(user_profile)
    return user_profile


def delete_user_profile(db_session: Session, user_id: str) -> Optional[UserProfile]:
    """
    Delete a user profile by their user ID.

    Args:
        db_session: The database session.
        user_id: The UUID of the user whose profile should be deleted.
    
    Returns:
        The deleted profile object or None if it was not found.
    """
    profile_to_delete = get_user_profile(db_session, user_id)
    if profile_to_delete:
        db_session.delete(profile_to_delete)
        db_session.commit()
    return profile_to_delete
