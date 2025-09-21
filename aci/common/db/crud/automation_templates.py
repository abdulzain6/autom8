from __future__ import annotations
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import select, func
from aci.common.db.sql_models import AutomationTemplate, App
from aci.common.schemas.automation_templates import (
    AutomationTemplateUpsert,
)


def _validate_and_fetch_apps_by_name(db: Session, app_names: List[str]) -> List[App]:
    """Validates that a list of App names exist and returns the App objects."""
    if not app_names:
        return []

    stmt = select(App).where(App.name.in_(app_names))
    apps = list(db.execute(stmt).scalars().all())

    found_names = {app.name for app in apps if app.has_configuration}
    missing_names = set(app_names) - found_names
    if missing_names:
        raise ValueError(
            f"Required apps not found by name / Not Configured: {list(missing_names)}"
        )

    return apps


def get_template_by_name(db: Session, name: str) -> Optional[AutomationTemplate]:
    """Retrieves a single automation template by its unique name."""
    stmt = select(AutomationTemplate).where(AutomationTemplate.name == name)
    return db.execute(stmt).scalar_one_or_none()


def get_all_templates(db: Session) -> List[AutomationTemplate]:
    """Lists all automation templates without pagination."""
    stmt = select(AutomationTemplate).order_by(AutomationTemplate.name)
    return list(db.execute(stmt).scalars().all())


def get_template(db: Session, template_id: str) -> Optional[AutomationTemplate]:
    """Retrieves a single automation template by its ID."""
    return db.get(AutomationTemplate, template_id)


def list_templates(
    db: Session,
    limit: int,
    offset: int,
    category: Optional[str] = None,
    search_query: Optional[str] = None,
) -> List[AutomationTemplate]:
    """
    Lists all automation templates with pagination, optional category filtering,
    and full-text search.
    """
    stmt = select(AutomationTemplate)

    if category:
        # Check if the category exists in the tags array using array_position
        stmt = stmt.where(func.array_position(AutomationTemplate.tags, category).isnot(None))

    if search_query:
        stmt = stmt.where(
            AutomationTemplate.search_vector.op("@@")(
                func.websearch_to_tsquery("english", search_query)
            )
        )

    # Order by category (first tag) when no specific category is selected, otherwise by name
    if category:
        stmt = stmt.order_by(AutomationTemplate.name)
    else:
        # When no category is specified, order by the first tag to group by category
        stmt = stmt.order_by(AutomationTemplate.tags[0], AutomationTemplate.name)

    stmt = stmt.offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_all_categories(db: Session) -> List[str]:
    """Retrieves a distinct, sorted list of all tags/categories from all templates."""
    # This query unnests the tags array and selects the distinct values.
    stmt = (
        select(func.unnest(AutomationTemplate.tags).label("category"))
        .distinct()
        .order_by("category")
    )
    results = db.execute(stmt).scalars().all()
    return list(results)


def create_template(
    db: Session, template_in: AutomationTemplateUpsert
) -> AutomationTemplate:
    """Creates a new automation template."""
    payload = template_in.model_dump()
    app_names = payload.pop("required_app_names", [])

    required_apps = _validate_and_fetch_apps_by_name(db, app_names)

    new_template = AutomationTemplate(**payload)
    new_template.required_apps = required_apps

    db.add(new_template)
    db.commit()  # Flush to get ID and other defaults
    return new_template


def update_template(
    db: Session,
    existing_template: AutomationTemplate,
    template_in: AutomationTemplateUpsert,
) -> AutomationTemplate:
    """Updates an existing automation template."""
    update_data = template_in.model_dump(exclude_unset=True)

    if "required_app_names" in update_data:
        app_names = update_data.pop("required_app_names") or []
        existing_template.required_apps = _validate_and_fetch_apps_by_name(
            db, app_names
        )

    for key, value in update_data.items():
        setattr(existing_template, key, value)

    db.commit()
    return existing_template


def delete_template_by_name(db: Session, name: str) -> None:
    """Deletes an automation template by its unique name."""
    template = get_template_by_name(db, name)
    if template:
        db.delete(template)
        db.commit()


def delete_template(db: Session, template_id: str) -> None:
    """Deletes an automation template by its ID."""
    template = get_template(db, template_id)
    if template:
        db.delete(template)
        db.commit()
        db.commit()
