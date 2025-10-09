from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, update, and_, or_

from aci.common.db.sql_models import Plan
from aci.common.enums import PlanDuration
from aci.common.logging_setup import get_logger

logger = get_logger(__name__)


def create_plan(
    db_session: Session,
    name: str,
    duration: PlanDuration,
    features: List[str],
    price: int,
    revenue_cat_product_id: str,
    automation_runs_limit: int,
    voice_chat_minutes_limit: int,
    automations_limit: int,
    description: Optional[str] = None,
    trial_days: Optional[int] = None,
    apps_limit: Optional[int] = None,
    active: bool = True,
    is_popular: bool = False,
    display_order: int = 0,
) -> Plan:
    """Create a new plan."""
    logger.debug(f"Creating plan: {name}")

    plan = Plan(
        name=name,
        duration=duration,
        features=features,
        price=price,
        revenue_cat_product_id=revenue_cat_product_id,
        automation_runs_limit=automation_runs_limit,
        voice_chat_minutes_limit=voice_chat_minutes_limit,
        automations_limit=automations_limit,
        description=description,
        trial_days=trial_days,
        apps_limit=apps_limit,
        active=active,
        is_popular=is_popular,
        display_order=display_order,
    )

    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


def get_plan_by_id(db_session: Session, plan_id: str) -> Optional[Plan]:
    """Get a plan by ID."""
    return db_session.execute(select(Plan).filter(Plan.id == plan_id)).scalar_one_or_none()


def get_plan_by_revenue_cat_id(db_session: Session, revenue_cat_product_id: str) -> Optional[Plan]:
    """Get a plan by RevenueCat product ID."""
    return db_session.execute(
        select(Plan).filter(Plan.revenue_cat_product_id == revenue_cat_product_id)
    ).scalar_one_or_none()


def get_plan_by_name(db_session: Session, name: str) -> Optional[Plan]:
    """Get a plan by name."""
    return db_session.execute(select(Plan).filter(Plan.name == name)).scalar_one_or_none()


def get_all_plans(
    db_session: Session,
    active_only: bool = True,
    include_inactive: bool = False
) -> List[Plan]:
    """Get all plans, optionally filtering by active status."""
    query = select(Plan)

    if active_only and not include_inactive:
        query = query.filter(Plan.active == True)
    elif include_inactive:
        # Include both active and inactive
        pass
    else:
        # active_only=False means get all
        pass

    query = query.order_by(Plan.display_order, Plan.created_at)
    return list(db_session.execute(query).scalars())


def get_popular_plans(db_session: Session, active_only: bool = True) -> List[Plan]:
    """Get plans marked as popular."""
    query = select(Plan).filter(Plan.is_popular == True)

    if active_only:
        query = query.filter(Plan.active == True)

    query = query.order_by(Plan.display_order, Plan.created_at)
    return list(db_session.execute(query).scalars())


def update_plan(
    db_session: Session,
    plan: Plan,
    name: Optional[str] = None,
    duration: Optional[PlanDuration] = None,
    features: Optional[List[str]] = None,
    price: Optional[int] = None,
    revenue_cat_product_id: Optional[str] = None,
    automation_runs_limit: Optional[int] = None,
    voice_chat_minutes_limit: Optional[int] = None,
    automations_limit: Optional[int] = None,
    description: Optional[str] = None,
    trial_days: Optional[int] = None,
    apps_limit: Optional[int] = None,
    active: Optional[bool] = None,
    is_popular: Optional[bool] = None,
    display_order: Optional[int] = None,
) -> Plan:
    """Update a plan with the provided fields."""
    logger.debug(f"Updating plan: {plan.id}")

    update_data = {}
    if name is not None:
        update_data["name"] = name
    if duration is not None:
        update_data["duration"] = duration
    if features is not None:
        update_data["features"] = features
    if price is not None:
        update_data["price"] = price
    if revenue_cat_product_id is not None:
        update_data["revenue_cat_product_id"] = revenue_cat_product_id
    if automation_runs_limit is not None:
        update_data["automation_runs_limit"] = automation_runs_limit
    if voice_chat_minutes_limit is not None:
        update_data["voice_chat_minutes_limit"] = voice_chat_minutes_limit
    if automations_limit is not None:
        update_data["automations_limit"] = automations_limit
    if description is not None:
        update_data["description"] = description
    if trial_days is not None:
        update_data["trial_days"] = trial_days
    if apps_limit is not None:
        update_data["apps_limit"] = apps_limit
    if active is not None:
        update_data["active"] = active
    if is_popular is not None:
        update_data["is_popular"] = is_popular
    if display_order is not None:
        update_data["display_order"] = display_order

    if update_data:
        db_session.execute(
            update(Plan).where(Plan.id == plan.id).values(**update_data)
        )
        db_session.commit()
        db_session.refresh(plan)

    return plan


def delete_plan(db_session: Session, plan: Plan) -> None:
    """Delete a plan."""
    logger.debug(f"Deleting plan: {plan.id}")
    db_session.delete(plan)
    db_session.commit()


def deactivate_plan(db_session: Session, plan: Plan) -> Plan:
    """Deactivate a plan (soft delete)."""
    logger.debug(f"Deactivating plan: {plan.id}")
    return update_plan(db_session, plan, active=False)


def activate_plan(db_session: Session, plan: Plan) -> Plan:
    """Activate a plan."""
    logger.debug(f"Activating plan: {plan.id}")
    return update_plan(db_session, plan, active=True)


def get_plans_by_duration(
    db_session: Session,
    duration: PlanDuration,
    active_only: bool = True
) -> List[Plan]:
    """Get all plans with a specific duration."""
    query = select(Plan).filter(Plan.duration == duration)

    if active_only:
        query = query.filter(Plan.active == True)

    query = query.order_by(Plan.display_order, Plan.created_at)
    return list(db_session.execute(query).scalars())


def search_plans(
    db_session: Session,
    search_term: Optional[str] = None,
    duration: Optional[PlanDuration] = None,
    active_only: bool = True,
    limit: Optional[int] = None
) -> List[Plan]:
    """Search plans by name or description."""
    query = select(Plan)

    if active_only:
        query = query.filter(Plan.active == True)

    if search_term:
        search_filter = or_(
            Plan.name.ilike(f"%{search_term}%"),
            Plan.description.ilike(f"%{search_term}%")
        )
        query = query.filter(search_filter)

    if duration:
        query = query.filter(Plan.duration == duration)

    query = query.order_by(Plan.display_order, Plan.created_at)

    if limit:
        query = query.limit(limit)

    return list(db_session.execute(query).scalars())