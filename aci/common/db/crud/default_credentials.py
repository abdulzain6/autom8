from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aci.common.db.sql_models import DefaultAppCredential
from aci.common.logging_setup import get_logger
from aci.common.schemas.app import DefaultAppCredentialCreate

logger = get_logger(__name__)


def create_default_app_credential(
    db_session: Session,
    app_id: str,
    credential_data: DefaultAppCredentialCreate,
) -> DefaultAppCredential:
    """
    Create default credentials for a specific app.
    An app can only have one set of default credentials.
    """
    logger.debug(f"Creating default credentials for app_id={app_id}")

    # The unique constraint on app_id will prevent duplicates.
    # The calling function should handle potential IntegrityError.
    credential = DefaultAppCredential(
        app_id=app_id,
        security_scheme=credential_data.security_scheme,
        credentials=credential_data.credentials.model_dump(mode="json"),
    )
    db_session.add(credential)
    db_session.flush()

    return credential


def get_default_app_credential_by_app_id(
    db_session: Session, app_id: str
) -> DefaultAppCredential | None:
    """
    Get the default credentials for a specific app by its ID.
    """
    statement = select(DefaultAppCredential).filter(DefaultAppCredential.app_id == app_id)
    return db_session.execute(statement).scalar_one_or_none()


def delete_default_app_credential_by_app_id(db_session: Session, app_id: str) -> int:
    """
    Delete the default credentials for a specific app by its ID.
    Returns the number of rows deleted.
    """
    logger.warning(f"Deleting default credentials for app_id={app_id}")
    statement = delete(DefaultAppCredential).filter(DefaultAppCredential.app_id == app_id)
    result = db_session.execute(statement)
    return result.rowcount

