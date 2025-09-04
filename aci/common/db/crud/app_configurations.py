from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aci.common.db.sql_models import App, AppConfiguration
from aci.common.logging_setup import get_logger
from aci.common.schemas.app_configurations import (
    AppConfigurationCreate,
    AppConfigurationUpdate,
)

logger = get_logger(__name__)


def delete_app_configurations_by_app_id(db_session: Session, app_id: str) -> None:
    """
    Delete all app configurations associated with a specific app_id.
    """
    logger.info(f"Deleting all app configurations for app_id='{app_id}'")
    statement = delete(AppConfiguration).where(AppConfiguration.app_id == app_id)
    db_session.execute(statement)
    db_session.commit()

def create_app_configuration(
    db_session: Session, app_configuration_create: AppConfigurationCreate
) -> AppConfiguration:
    """
    Create a new app configuration record.
    """
    # Get the corresponding app_id from the app's name
    app_id = db_session.execute(
        select(App.id).filter_by(name=app_configuration_create.app_name)
    ).scalar_one()

    # Create the AppConfiguration instance without project_id
    app_configuration = AppConfiguration(
        app_id=app_id,
        security_scheme=app_configuration_create.security_scheme,
        security_scheme_overrides=app_configuration_create.security_scheme_overrides.model_dump(
            exclude_none=True
        ), # type: ignore
        enabled=True,
        all_functions_enabled=app_configuration_create.all_functions_enabled,
        enabled_functions=app_configuration_create.enabled_functions,
    )
    db_session.add(app_configuration)
    db_session.commit()
    db_session.refresh(app_configuration)

    return app_configuration


def update_app_configuration(
    db_session: Session,
    app_configuration: AppConfiguration,
    update: AppConfigurationUpdate,
) -> AppConfiguration:
    """
    Update an app configuration.
    Fields from the `update` model that are not set will not be changed.
    """
    # Get a dictionary of fields that were actually set in the update request
    update_data = update.model_dump(exclude_unset=True)

    # Dynamically update the model's attributes
    for key, value in update_data.items():
        setattr(app_configuration, key, value)

    db_session.commit()
    db_session.refresh(app_configuration)

    return app_configuration


def delete_app_configuration(db_session: Session, app_name: str) -> None:
    """
    Delete an app configuration by the app name.
    """
    statement = (
        select(AppConfiguration)
        .join(App, AppConfiguration.app_id == App.id)
        .filter(App.name == app_name)
    )
    app_to_delete = db_session.execute(statement).scalar_one()
    db_session.delete(app_to_delete)
    db_session.commit()


def get_app_configurations(
    db_session: Session,
    app_names: list[str] | None,
    limit: int,
    offset: int,
) -> list[AppConfiguration]:
    """
    Get all app configurations, optionally filtered by app names.
    """
    statement = select(AppConfiguration)
    if app_names:
        statement = statement.join(App, AppConfiguration.app_id == App.id).filter(
            App.name.in_(app_names)
        )
    statement = statement.offset(offset).limit(limit)
    app_configurations = list(db_session.execute(statement).scalars().all())
    return app_configurations


def get_app_configuration(
    db_session: Session, app_name: str
) -> AppConfiguration | None:
    """
    Get an app configuration by app name.
    """
    app_configuration: AppConfiguration | None = db_session.execute(
        select(AppConfiguration)
        .join(App, AppConfiguration.app_id == App.id)
        .filter(App.name == app_name)
    ).scalar_one_or_none()
    return app_configuration


def get_app_configurations_by_app_id(
    db_session: Session, app_id: str
) -> list[AppConfiguration]:
    """
    Get all app configurations for a specific app_id.
    (This function did not require changes)
    """
    statement = select(AppConfiguration).filter(AppConfiguration.app_id == app_id)
    return list(db_session.execute(statement).scalars().all())


def app_configuration_exists(db_session: Session, app_name: str) -> bool | None:
    """
    Check if an app configuration exists for a given app name.
    """
    stmt = (
        select(AppConfiguration)
        .join(App, AppConfiguration.app_id == App.id)
        .filter(App.name == app_name)
    )
    return db_session.execute(select(stmt.exists())).scalar()