from typing import List, Optional, Tuple, Dict
from sqlalchemy import and_, null, select, update, func
from sqlalchemy.orm import Session, selectinload

from aci.common.db.sql_models import App, LinkedAccount, Function, AppConfiguration, DefaultAppCredential, AutomationTemplate, automation_template_apps
from aci.common.logging_setup import get_logger
from aci.common.schemas.app import AppUpsert

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
    db_session.flush()
    db_session.refresh(app)
    return app


def update_app(
    db_session: Session,
    app: App,
    app_upsert: AppUpsert,
    app_embedding: list[float] | None = None,
) -> App:
    new_app_data = app_upsert.model_dump(mode="json", exclude_unset=True)
    for field, value in new_app_data.items():
        setattr(app, field, value)
    if app_embedding is not None:
        app.embedding = app_embedding
    db_session.flush()
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

    templates_stmt = (
        select(AutomationTemplate)
        .join(automation_template_apps)
        .where(automation_template_apps.c.app_id == app.id)
        .order_by(AutomationTemplate.name)
        .limit(5)
    )
    related_templates = list(db_session.execute(templates_stmt).scalars().all())

    return app, linked_account, related_templates


def _fetch_and_map_related_templates(db: Session, apps: List[App]) -> Dict[str, List[AutomationTemplate]]:
    """
    Given a list of apps, efficiently fetches all related templates and maps them by app_id.
    """
    if not apps:
        return {}
    
    app_ids = [app.id for app in apps]
    
    # Subquery to rank templates for each app and select the top 5
    template_subquery = (
        select(
            automation_template_apps.c.app_id,
            AutomationTemplate,
            func.row_number().over(
                partition_by=automation_template_apps.c.app_id,
                order_by=AutomationTemplate.name,
            ).label("rn")
        )
        .join(AutomationTemplate)
        .where(automation_template_apps.c.app_id.in_(app_ids))
        .subquery("ranked_templates")
    )
    
    # Final query to get the top 5 templates
    templates_stmt = select(
        template_subquery.c.app_id,
        AutomationTemplate
    ).select_from(template_subquery).where(template_subquery.c.rn <= 5)
    
    results = db.execute(templates_stmt).all()
    
    templates_by_app_id: Dict[str, List[AutomationTemplate]] = {app_id: [] for app_id in app_ids}
    for app_id, template in results:
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
            selectinload(App.default_credentials)
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
