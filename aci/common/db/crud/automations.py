from typing import Optional, List
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.orm import Session
from sqlalchemy import select
from aci.common.db import crud
from aci.common.db.sql_models import (
    App,
    Automation,
    AutomationRun,
    AutomationLinkedAccount,
    LinkedAccount,
    Artifact,
)
from aci.common.schemas.automations import (
    AutomationCreate,
    AutomationFromTemplateCreate,
    AutomationUpdate,
)
from . import automation_templates

# Jinja2 environment for rendering goals from templates
jinja_env = SandboxedEnvironment()


def _validate_and_fetch_linked_accounts(
    db: Session, user_id: str, linked_account_ids: List[str]
) -> List[LinkedAccount]:
    """
    Validates that a list of LinkedAccount IDs exist and belong to the specified user.
    """
    if not linked_account_ids:
        return []

    stmt = select(LinkedAccount).where(LinkedAccount.id.in_(linked_account_ids))

    linked_accounts: List[LinkedAccount] = list(db.execute(stmt).scalars().all())

    found_ids = {la.id for la in linked_accounts}
    missing_ids = set(linked_account_ids) - found_ids
    if missing_ids:
        raise ValueError(f"Linked accounts not found: {list(missing_ids)}")

    for la in linked_accounts:
        if la.user_id != user_id:
            raise ValueError(
                f"Linked account {la.id} does not belong to user {user_id}"
            )
    return linked_accounts


def get_automation(db: Session, automation_id: str) -> Optional[Automation]:
    """Retrieves a single automation by its ID using the efficient db.get method."""
    return db.get(Automation, automation_id)


def list_user_automations(
    db: Session, user_id: str, limit: int, offset: int
) -> List[Automation]:
    """
    Lists all automations for a given user with pagination.
    """
    stmt = (
        select(Automation)
        .where(Automation.user_id == user_id)
        .offset(offset)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def create_automation(
    db: Session, user_id: str, automation_in: AutomationCreate
) -> Automation:
    """Creates a new automation and associates linked accounts."""
    payload = automation_in.model_dump()
    linked_account_ids = payload.pop("linked_account_ids", [])

    linked_accounts = _validate_and_fetch_linked_accounts(
        db, user_id, linked_account_ids
    )

    new_automation = Automation(user_id=user_id, **payload)
    db.add(new_automation)
    db.flush()

    assoc_objs = [
        AutomationLinkedAccount(
            automation_id=new_automation.id,
            linked_account_id=la.id,
        )
        for la in linked_accounts
    ]
    if assoc_objs:
        db.add_all(assoc_objs)

    db.commit()
    db.refresh(new_automation)
    return new_automation


def update_automation(
    db: Session, automation_id: str, automation_in: AutomationUpdate
) -> Automation:
    """Updates an automation's fields and its linked account associations."""
    automation = get_automation(db, automation_id)
    if not automation:
        raise ValueError(f"Automation {automation_id} not found")

    update_data = automation_in.model_dump(exclude_unset=True)
    is_recurring = update_data.get("is_recurring", automation.is_recurring)
    
    final_cron_schedule = update_data.get("cron_schedule", automation.cron_schedule)
    if "cron_schedule" in update_data:
        final_cron_schedule = update_data["cron_schedule"]

    if is_recurring and not final_cron_schedule:
        raise ValueError("A cron_schedule must be provided for a recurring automation.")
    
    if update_data.get("is_recurring") is False:
        update_data['cron_schedule'] = None

    if "linked_account_ids" in update_data:
        # Gracefully handle if linked_account_ids is None
        new_ids = update_data.pop("linked_account_ids") or []
        new_linked_account_ids = set(new_ids)

        _validate_and_fetch_linked_accounts(
            db, automation.user_id, list(new_linked_account_ids)
        )

        current_assoc_stmt = select(AutomationLinkedAccount).where(
            AutomationLinkedAccount.automation_id == automation_id
        )
        current_assocs = db.execute(current_assoc_stmt).scalars().all()
        current_linked_ids = {assoc.linked_account_id for assoc in current_assocs}

        ids_to_remove = current_linked_ids - new_linked_account_ids
        for assoc in current_assocs:
            if assoc.linked_account_id in ids_to_remove:
                db.delete(assoc)

        ids_to_add = new_linked_account_ids - current_linked_ids
        new_assocs = [
            AutomationLinkedAccount(
                automation_id=automation_id, linked_account_id=la_id
            )
            for la_id in ids_to_add
        ]
        if new_assocs:
            db.add_all(new_assocs)

    for key, value in update_data.items():
        setattr(automation, key, value)

    db.commit()
    db.refresh(automation)
    return automation


def delete_automation(db: Session, automation_id: str) -> None:
    """Deletes an automation by its ID."""
    automation = get_automation(db, automation_id)
    if automation:
        db.delete(automation)
        db.commit()


def get_automation_artifacts(db: Session, automation_id: str) -> List[Artifact]:
    """Gets all unique artifacts associated with any run of an automation."""
    stmt = (
        select(Artifact)
        .join(Artifact.automation_runs)
        .join(AutomationRun.automation)
        .where(Automation.id == automation_id)
        .distinct()
    )
    return list(db.execute(stmt).scalars().all())


def create_automation_from_template(
    db: Session, user_id: str, template_data: AutomationFromTemplateCreate
) -> Automation:
    """Validates, renders, and creates an automation from a template."""
    # 1. Fetch and validate template
    template = automation_templates.get_template(db, template_data.template_id)
    if not template:
        raise ValueError(f"Template with id '{template_data.template_id}' not found.")

    # 2. Validate variables
    required_vars = set(template.variable_names)
    provided_vars = set(template_data.variables.keys())
    if required_vars != provided_vars:
        missing = required_vars - provided_vars
        extra = provided_vars - required_vars
        error_parts = []
        if missing:
            error_parts.append(f"missing required variables: {list(missing)}")
        if extra:
            error_parts.append(f"unexpected variables provided: {list(extra)}")
        raise ValueError(
            f"Variable mismatch for template '{template.name}'. Details: "
            + ", ".join(error_parts)
        )

    # 3. Render the goal
    jinja_template = jinja_env.from_string(template.goal)
    rendered_goal = jinja_template.render(template_data.variables)

    # 4. Validate linked accounts against template requirements
    required_app_ids = {app.id for app in template.required_apps}
    if required_app_ids:  # Only validate if the template requires apps
        linked_accounts = _validate_and_fetch_linked_accounts(
            db, user_id, template_data.linked_account_ids
        )
        provided_app_ids = {la.app_id for la in linked_accounts}

        if not required_app_ids.issubset(provided_app_ids):
            missing_app_ids = required_app_ids - provided_app_ids
            # Fetch app names for a user-friendly error message
            missing_apps = db.query(App.name).filter(App.id.in_(missing_app_ids)).all()
            missing_app_names = [name for (name,) in missing_apps]
            raise ValueError(
                f"Missing linked accounts for required apps: {missing_app_names}"
            )

    # 5. Create the automation object
    automation_to_create = AutomationCreate(
        name=template_data.name,
        goal=rendered_goal,
        linked_account_ids=template_data.linked_account_ids,
        is_recurring=template_data.is_recurring,
        cron_schedule=template_data.cron_schedule,
        is_deep=template.is_deep,
    )

    return create_automation(db, user_id, automation_to_create)
