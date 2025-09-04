from typing import List, Optional, Tuple, Dict
from sqlalchemy import and_, null, select, update, func
from sqlalchemy.orm import Session, selectinload

from aci.common.db.sql_models import (
    App,
    LinkedAccount,
    AppConfiguration,
    AutomationTemplate,
    automation_template_apps,
)
from aci.common.logging_setup import get_logger
from aci.common.schemas.app import AppUpsert
import jsonschema

logger = get_logger(__name__)


def create_app(
    db_session: Session,
    app_upsert: AppUpsert,
    app_embedding: list[float],
) -> App:
    logger.debug(f"Creating app: {app_upsert}")
    app_data = app_upsert.model_dump(mode="json", exclude_none=True)
    app = App(**app_data, embedding=app_embedding)
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)
    return app


def update_app(
    db_session: Session,
    app: App,
    app_upsert: AppUpsert,
    app_embedding: list[float] | None = None,
) -> App:
    """Updates an app, validating its configuration schema if provided."""
    new_app_data = app_upsert.model_dump(mode="json", exclude_unset=True)

    if "configuration_schema" in new_app_data and new_app_data["configuration_schema"]:
        try:
            jsonschema.Draft7Validator.check_schema(
                new_app_data["configuration_schema"]
            )
        except jsonschema.SchemaError as e:
            raise ValueError(f"Invalid JSON Schema for configuration_schema: {e}")

    for field, value in new_app_data.items():
        setattr(app, field, value)
    if app_embedding is not None:
        app.embedding = app_embedding
    db_session.commit()
    db_session.refresh(app)
    return app


def get_app(db_session: Session, app_name: str, active_only: bool) -> App | None:
    statement = select(App).filter_by(name=app_name)
    if active_only:
        statement = statement.filter(App.active)
    app: App | None = db_session.execute(statement).scalar_one_or_none()
    return app


def get_app_with_user_context(
    db_session: Session, app_name: str, user_id: str, active_only: bool
) -> Optional[Tuple[App, Optional[LinkedAccount], List[AutomationTemplate]]]:
    """
    Efficiently retrieves a single App, the user's LinkedAccount for it,
    and a list of related automation templates.
    """
    stmt = (
        select(App, LinkedAccount)
        .outerjoin(
            LinkedAccount,
            and_(App.id == LinkedAccount.app_id, LinkedAccount.user_id == user_id),
        )
        .options(
            selectinload(App.functions),
            selectinload(App.configuration),
            selectinload(App.default_credentials),
        )
        .filter(App.name == app_name)
    )
    if active_only:
        stmt = stmt.filter(App.active)

    result = db_session.execute(stmt).first()

    if not result:
        return None

    app, linked_account = result

    # --- REFACTORED QUERY ---
    # This is a more expressive and direct way to get templates that require this app.
    templates_stmt = (
        select(AutomationTemplate)
        .join(
            automation_template_apps,
            automation_template_apps.c.template_id == AutomationTemplate.id,
        )
        .where(automation_template_apps.c.app_id == app.id)
        .order_by(AutomationTemplate.name)
        .limit(5)
    )
    related_templates = list(db_session.execute(templates_stmt).scalars().all())

    return app, linked_account, related_templates


def _fetch_and_map_related_templates(
    db: Session, apps: List[App]
) -> Dict[str, List[AutomationTemplate]]:
    """
    Given a list of apps, efficiently fetches all related templates and maps them by app_id.
    This uses a single query and processes the results in Python to avoid N+1 queries.
    """
    if not apps:
        return {}

    app_ids = [app.id for app in apps]

    # Get all (template, app_id) pairs for the given list of apps
    templates_stmt = (
        select(AutomationTemplate, automation_template_apps.c.app_id)
        .join(
            automation_template_apps,
            automation_template_apps.c.template_id == AutomationTemplate.id,
        )
        .where(automation_template_apps.c.app_id.in_(app_ids))
        .order_by(automation_template_apps.c.app_id, AutomationTemplate.name)
    )

    all_pairs = db.execute(templates_stmt).all()

    # Process in Python to group by app_id and get the top 5 for each
    templates_by_app_id: Dict[str, List[AutomationTemplate]] = {
        app_id: [] for app_id in app_ids
    }
    for template, app_id in all_pairs:
        if len(templates_by_app_id[app_id]) < 5:
            templates_by_app_id[app_id].append(template)

    return templates_by_app_id


def list_apps_with_user_context(
    db: Session,
    user_id: str,
    active_only: bool = True,
    configured_only: bool = True,
    app_names: Optional[List[str]] = None,
    limit: int = 100,
    offset: int = 0,
    return_automation_templates: bool = False,
) -> List[Tuple[App, Optional[LinkedAccount], List[AutomationTemplate]]]:
    """
    Efficiently retrieves a list of Apps, optionally including related templates.
    """
    stmt = (
        select(App, LinkedAccount)
        .outerjoin(
            LinkedAccount,
            and_(App.id == LinkedAccount.app_id, LinkedAccount.user_id == user_id),
        )
        .options(
            selectinload(App.functions),
            selectinload(App.configuration),
            selectinload(App.default_credentials),
        )
        .order_by(App.name)
    )
    if active_only:
        stmt = stmt.where(App.active == True)
    if configured_only:
        stmt = stmt.where(App.configuration.has())
    if app_names:
        stmt = stmt.where(App.name.in_(app_names))
    stmt = stmt.limit(limit).offset(offset)

    app_results = db.execute(stmt).all()

    apps = [app for app, _ in app_results]
    templates_map = {}
    if return_automation_templates and apps:
        templates_map = _fetch_and_map_related_templates(db, apps)

    final_results = [
        (app, linked_account, templates_map.get(app.id, []))
        for app, linked_account in app_results
    ]

    return final_results


def search_apps(
    db_session: Session,
    user_id: str,
    active_only: bool,
    configured_only: bool,
    app_names: list[str] | None,
    categories: list[str] | None,
    intent_embedding: list[float] | None,
    limit: int,
    offset: int,
    return_automation_templates: bool = False,
) -> list[tuple[App, Optional[LinkedAccount], float | None, List[AutomationTemplate]]]:
    """
    Efficiently searches for apps, joining with user context and optionally including related templates.
    """
    statement = (
        select(App, LinkedAccount)
        .outerjoin(
            LinkedAccount,
            and_(App.id == LinkedAccount.app_id, LinkedAccount.user_id == user_id),
        )
        .options(
            selectinload(App.functions),
            selectinload(App.configuration),
            selectinload(App.default_credentials),
        )
    )

    if active_only:
        statement = statement.filter(App.active)
    if app_names is not None:
        statement = statement.filter(App.name.in_(app_names))
    if configured_only:
        statement = statement.filter(App.configuration.has())
    if categories is not None:
        statement = statement.filter(App.categories.overlap(categories))

    if intent_embedding is not None:
        similarity_score = App.embedding.cosine_distance(intent_embedding)
        statement = statement.add_columns(similarity_score.label("similarity_score"))
        statement = statement.order_by("similarity_score")
    else:
        statement = statement.add_columns(null().label("similarity_score"))
        statement = statement.order_by(App.name)

    statement = statement.offset(offset).limit(limit)
    search_results = db_session.execute(statement).all()

    apps = [app for app, _, __ in search_results]
    templates_map = {}
    if return_automation_templates and apps:
        templates_map = _fetch_and_map_related_templates(db_session, apps)

    final_results = [
        (app, linked_account, score, templates_map.get(app.id, []))
        for app, linked_account, score in search_results
    ]

    return final_results


def set_app_active_status(db_session: Session, app_name: str, active: bool) -> None:
    statement = update(App).filter_by(name=app_name).values(active=active)
    db_session.execute(statement)


def get_user_linked_app_names(db: Session, user_id: str) -> List[str]:
    """
    Efficiently retrieves a list of app names that a user has linked.

    This performs a single, simple query to get the names of all apps
    for which a user has a LinkedAccount record.

    Args:
        db: The SQLAlchemy database session.
        user_id: The ID of the user.

    Returns:
        A list of unique app names the user has connected, sorted alphabetically.
    """
    stmt = (
        select(App.name)
        .join(LinkedAccount, App.id == LinkedAccount.app_id)
        .where(LinkedAccount.user_id == user_id)
        .order_by(App.name)
        .distinct()
    )

    app_names = db.execute(stmt).scalars().all()
    return list(app_names)


def get_user_linked_apps_with_functions(db: Session, user_id: str) -> List[App]:
    """
    Efficiently retrieves a list of App objects that a user has linked,
    eagerly loading their functions.

    This performs a single query to get the apps for which a user has a
    LinkedAccount record and pre-loads all associated function data.

    Args:
        db: The SQLAlchemy database session.
        user_id: The ID of the user.

    Returns:
        A list of App objects the user has connected, sorted alphabetically by name.
    """
    stmt = (
        select(App)
        .join(LinkedAccount, App.id == LinkedAccount.app_id)
        .where(LinkedAccount.user_id == user_id)
        .options(selectinload(App.functions))  # Eagerly load the functions
        .order_by(App.name)
        .distinct()
    )

    apps = db.execute(stmt).scalars().all()
    return list(apps)


def get_apps_with_functions_by_names(
    db_session: Session, app_names: List[str]
) -> List[App]:
    """
    Efficiently fetches apps and their associated functions for a given list of app names.
    """
    if not app_names:
        return []

    stmt = (
        select(App).options(selectinload(App.functions)).where(App.name.in_(app_names))
    )
    return list(db_session.execute(stmt).scalars().all())
