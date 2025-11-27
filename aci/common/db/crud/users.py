from sqlalchemy.orm import Session
from aci.common.db.sql_models import SupabaseUser, UserProfile, FCMToken, LinkedAccount, Artifact, Automation, UserUsage, WebhookEvent
from aci.common.logging_setup import get_logger

logger = get_logger(__name__)

def delete_user_data(db: Session, user_id: str):
    """
    Deletes a user and all their associated data from the database.
    Note: This does not delete from Supabase auth; that should be handled separately.
    """
    user = db.query(SupabaseUser).filter(SupabaseUser.id == user_id).first()
    if not user:
        return False

    # Delete UserProfile
    db.query(UserProfile).filter(UserProfile.id == user_id).delete()

    # Delete FCMTokens
    db.query(FCMToken).filter(FCMToken.user_id == user_id).delete()

    # Delete LinkedAccounts (and their associated Secrets due to cascade)
    db.query(LinkedAccount).filter(LinkedAccount.user_id == user_id).delete()

    # Delete Automations (and their associated AutomationRuns and AutomationLinkedAccounts due to cascade)
    db.query(Automation).filter(Automation.user_id == user_id).delete()

    # Delete Artifacts
    db.query(Artifact).filter(Artifact.user_id == user_id).delete()

    # Delete UserUsage
    db.query(UserUsage).filter(UserUsage.user_id == user_id).delete()

    # Delete WebhookEvents associated with the user
    db.query(WebhookEvent).filter(WebhookEvent.user_id == user_id).delete()

    # Finally, delete the SupabaseUser itself
    db.delete(user)
    db.commit()
    logger.info(f"User and all associated data for user_id {user_id} deleted successfully from database.")
    return True
