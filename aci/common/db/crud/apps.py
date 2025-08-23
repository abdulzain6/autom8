from typing import List, Optional, Tuple
from sqlalchemy import and_, null, select, update
from sqlalchemy.orm import Session, selectinload

from aci.common.db.sql_models import App, LinkedAccount, Function, AppConfiguration, DefaultAppCredential
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
) -> Tuple[App, Optional[LinkedAccount]] | None:
    """
    Efficiently retrieves a single App and the user's LinkedAccount for it in one query.
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
    return result if result else None # type: ignore


def list_apps_with_user_context(
    db: Session,
    user_id: str,
    active_only: bool = True,
    configured_only: bool = True,
    app_names: Optional[List[str]] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Tuple[App, Optional[LinkedAccount]]]:
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
        stmt = stmt.where(App.configuration != None)
    if app_names:
        stmt = stmt.where(App.name.in_(app_names))
    stmt = stmt.limit(limit).offset(offset)
    results = db.execute(stmt).all()
    return results # type: ignore


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
) -> list[tuple[App, Optional[LinkedAccount], float | None]]:
    """
    Efficiently searches for apps, joining with the user's linked account info.
    """
    statement = (
        select(App, LinkedAccount)
        .outerjoin(
            LinkedAccount,
            and_(App.id == LinkedAccount.app_id, LinkedAccount.user_id == user_id),
        )
        .options(selectinload(App.functions)) # Eagerly load functions
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
    logger.debug(f"Executing statement, statement={statement}")
    results = db_session.execute(statement).all()
    
    # The result is a list of Row objects, which can be unpacked
    return [(app, linked_account, score) for app, linked_account, score in results]


def set_app_active_status(db_session: Session, app_name: str, active: bool) -> None:
    statement = update(App).filter_by(name=app_name).values(active=active)
    db_session.execute(statement)

