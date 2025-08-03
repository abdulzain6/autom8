from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from aci.common import validators
from aci.common.db.sql_models import App, LinkedAccount
from aci.common.enums import SecurityScheme
from aci.common.logging_setup import get_logger
from aci.common.schemas.linked_accounts import LinkedAccountUpdate
from aci.common.schemas.security_scheme import (
    APIKeySchemeCredentials,
    NoAuthSchemeCredentials,
    OAuth2SchemeCredentials,
)

logger = get_logger(__name__)


def get_linked_accounts(
    db_session: Session,
    user_id: str,
    app_name: str | None = None,
) -> list[LinkedAccount]:
    """
    Get all linked accounts for a user, with an optional filter for app_name.
    """
    statement = select(LinkedAccount).filter_by(user_id=user_id)
    if app_name:
        statement = statement.join(App, LinkedAccount.app_id == App.id).filter(
            App.name == app_name
        )

    return list(db_session.execute(statement).scalars().all())


def get_linked_account(
    db_session: Session, user_id: str, app_name: str
) -> LinkedAccount | None:
    """
    Get a single linked account by the user's ID and the application's name.
    """
    app_id_stmt = select(App.id).filter_by(name=app_name).scalar_subquery()
    statement = select(LinkedAccount).filter_by(user_id=user_id, app_id=app_id_stmt)
    linked_account: LinkedAccount | None = db_session.execute(
        statement
    ).scalar_one_or_none()

    return linked_account


def get_linked_account_by_id_and_user_id(
    db_session: Session, user_id: str, linked_account_id: str
) -> LinkedAccount | None:
    """
    Get a single linked account by its ID and the user's ID.
    """
    statement = select(LinkedAccount).filter_by(id=linked_account_id, user_id=user_id)
    return db_session.execute(statement).scalar_one_or_none()


def get_linked_account_by_pk(
    db_session: Session, user_id: str, app_id: str
) -> LinkedAccount | None:
    """
    Get a single linked account by its composite primary key (user_id, app_id).
    """
    return db_session.get(LinkedAccount, (user_id, app_id))


def get_linked_accounts_by_app_id(
    db_session: Session, app_id: str
) -> list[LinkedAccount]:
    """
    Get all linked accounts for a specific app across all users.
    """
    statement = select(LinkedAccount).filter_by(app_id=app_id)
    linked_accounts: list[LinkedAccount] = list(
        db_session.execute(statement).scalars().all()
    )
    return linked_accounts


def delete_linked_account(db_session: Session, linked_account: LinkedAccount) -> None:
    """
    Deletes a linked account instance from the database.
    """
    db_session.delete(linked_account)
    db_session.flush()


def create_linked_account(
    db_session: Session,
    user_id: str,
    app_name: str,
    security_scheme: SecurityScheme,
    security_credentials: (
        OAuth2SchemeCredentials
        | APIKeySchemeCredentials
        | NoAuthSchemeCredentials
        | None
    ) = None,
    enabled: bool = True,
) -> LinkedAccount:
    """
    Create a linked account for a user with a specific app.
    """
    app_id = db_session.execute(select(App.id).filter_by(name=app_name)).scalar_one()
    linked_account = LinkedAccount(
        user_id=user_id,
        app_id=app_id,
        security_scheme=security_scheme,
        security_credentials=(
            security_credentials.model_dump(mode="json") if security_credentials else {}
        ),
        enabled=enabled,
    )
    db_session.add(linked_account)
    db_session.flush()
    db_session.refresh(linked_account)
    return linked_account


def update_linked_account_credentials(
    db_session: Session,
    linked_account: LinkedAccount,
    security_credentials: (
        OAuth2SchemeCredentials | APIKeySchemeCredentials | NoAuthSchemeCredentials
    ),
) -> LinkedAccount:
    """
    Update the security credentials of a linked account.
    Removing the security credentials (setting it to empty dict) is not handled here.
    """
    # TODO: paranoid validation, should be removed if later the validation is done on the schema level
    validators.security_scheme.validate_scheme_and_credentials_type_match(
        linked_account.security_scheme, security_credentials
    )

    linked_account.security_credentials = security_credentials.model_dump(mode="json")
    db_session.flush()
    db_session.refresh(linked_account)
    return linked_account


def update_linked_account(
    db_session: Session,
    linked_account: LinkedAccount,
    linked_account_update: LinkedAccountUpdate,
) -> LinkedAccount:
    if linked_account_update.enabled is not None:
        linked_account.enabled = linked_account_update.enabled
    db_session.flush()
    db_session.refresh(linked_account)
    return linked_account


def update_linked_account_last_used_at(
    db_session: Session,
    last_used_at: datetime,
    linked_account: LinkedAccount,
) -> LinkedAccount:
    linked_account.last_used_at = last_used_at
    db_session.flush()
    db_session.refresh(linked_account)
    return linked_account


def delete_linked_accounts_by_app_name(db_session: Session, app_name: str) -> int:
    """
    Deletes all linked accounts associated with a specific app_name across all users.
    """
    app_id_stmt = select(App.id).filter_by(name=app_name).scalar_subquery()
    statement = select(LinkedAccount).filter(LinkedAccount.app_id == app_id_stmt)

    linked_accounts_to_delete = db_session.execute(statement).scalars().all()
    if not linked_accounts_to_delete:
        return 0

    for linked_account in linked_accounts_to_delete:
        db_session.delete(linked_account)
    db_session.flush()
    return len(linked_accounts_to_delete)
