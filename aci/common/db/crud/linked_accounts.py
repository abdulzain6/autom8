import jsonschema
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import exists, select
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
    configuration: Optional[Dict[str, Any]] = None,
) -> LinkedAccount:
    """
    Create a linked account for a user with a specific app, validating any
    provided configuration against the app's schema.
    """
    app = db_session.execute(select(App).filter_by(name=app_name)).scalar_one_or_none()
    if not app:
        raise ValueError(f"App with name '{app_name}' not found.")

    if app.configuration_schema:
        if configuration is None:
            raise ValueError("This app requires a configuration, but none was provided.")
        try:
            jsonschema.validate(instance=configuration, schema=app.configuration_schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Configuration is invalid: {e.message}")
    elif configuration is not None:
        raise ValueError("This app does not support user configurations.")

    linked_account = LinkedAccount(
        user_id=user_id,
        app_id=app.id,
        security_scheme=security_scheme,
        security_credentials=(
            security_credentials.model_dump(mode="json") if security_credentials else {}
        ),
        configuration=configuration,
        disabled_functions=[],
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
    """
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
    linked_account.disabled_functions = linked_account_update.disabled_functions
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


def linked_account_exists_for_app_and_user(
    db: Session, *, app_id: str, user_id: str
) -> bool:
    """
    Efficiently checks if a LinkedAccount exists for a specific app and user.
    """
    stmt = select(
        exists().where(LinkedAccount.app_id == app_id, LinkedAccount.user_id == user_id)
    )
    result = db.execute(stmt).scalar()
    return result is True


def get_linked_account_for_app_and_user(
    db: Session, *, app_id: str, user_id: str
) -> Optional[LinkedAccount]:
    """
    Efficiently retrieves a single LinkedAccount for a specific app and user.
    """
    stmt = select(LinkedAccount).where(
        LinkedAccount.app_id == app_id, LinkedAccount.user_id == user_id
    )
    return db.execute(stmt).scalar_one_or_none()


def get_linked_accounts_for_apps(
    db: Session, *, user_id: str, app_ids: List[str]
) -> List[LinkedAccount]:
    """
    Efficiently retrieves all of a user's linked accounts for a given list of app IDs.
    """
    if not app_ids:
        return []

    stmt = select(LinkedAccount).where(
        LinkedAccount.user_id == user_id, LinkedAccount.app_id.in_(app_ids)
    )
    return list(db.execute(stmt).scalars().all())
