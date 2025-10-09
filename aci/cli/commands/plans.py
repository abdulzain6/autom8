import json
from pathlib import Path

import click
from deepdiff import DeepDiff
from rich.console import Console
from sqlalchemy.orm import Session

from aci.cli import config
from aci.common import utils
from aci.common.db import crud
from aci.common.db.sql_models import Plan
from aci.common.schemas.plan import PlansFile, PlanCreate

console = Console()


@click.command()
@click.option(
    "--plans-file",
    "plans_file",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the plans definition JSON file.",
)
def upsert_plans(plans_file: Path):
    """
    Syncs Plans in the DB from a JSON file.

    - Creates plans from the file that are not in the DB.
    - Updates plans in the DB that have changed in the file.
    - Deactivates plans in the DB that are not present in the file (instead of deleting).
    """
    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        try:
            with open(plans_file) as f:
                plans_data = json.load(f)

            # Validate the entire file structure first
            validated_file = PlansFile.model_validate(plans_data)
            plans_from_file = validated_file.plans

        except Exception as e:
            console.print(f"[bold red]Error loading or validating plans file: {e}[/bold red]")
            raise click.Abort()

        console.rule("Starting plan sync process")

        # --- Sync Logic ---
        plan_revenue_cat_ids_from_file = {p.revenue_cat_product_id for p in plans_from_file}

        all_db_plans = crud.plans.get_all_plans(db_session, active_only=False, include_inactive=True)
        plan_revenue_cat_ids_from_db = {p.revenue_cat_product_id for p in all_db_plans}

        # 1. Handle Deactivations (instead of deletions)
        ids_to_deactivate = plan_revenue_cat_ids_from_db - plan_revenue_cat_ids_from_file
        if ids_to_deactivate:
            console.print("\n[bold yellow]Deactivating plans not found in file:[/bold yellow]")
            for revenue_cat_id in sorted(list(ids_to_deactivate)):
                plan_to_deactivate = crud.plans.get_plan_by_revenue_cat_id(
                    db_session, revenue_cat_id
                )
                if plan_to_deactivate:
                    crud.plans.deactivate_plan(db_session, plan_to_deactivate)
                    console.print(f"  - Deactivating plan '{plan_to_deactivate.name}' (RevenueCat ID: {revenue_cat_id}).")

        # 2. Handle Upserts
        console.print("\n[bold]Processing plans from file:[/bold]")
        for plan_upsert in plans_from_file:
            upsert_plan_helper(db_session, plan_upsert)

        try:
            db_session.commit()
            console.rule(f"[bold green]Successfully synced all plans.[/bold green]")
        except Exception as e:
            console.print(f"[bold red]An error occurred during commit: {e}[/bold red]")
            db_session.rollback()
            raise click.Abort()


def upsert_plan_helper(db_session: Session, plan_upsert: PlanCreate):
    """Orchestrates the upsert logic for a single plan."""
    try:
        existing_plan = crud.plans.get_plan_by_revenue_cat_id(
            db_session, plan_upsert.revenue_cat_product_id
        )

        if existing_plan is None:
            console.rule(f"Creating new Plan: {plan_upsert.name}")
            crud.plans.create_plan(db_session, **plan_upsert.model_dump())
            console.print(f"[green]  + Creating plan '{plan_upsert.name}'.[/green]")
        else:
            update_plan_helper(db_session, existing_plan, plan_upsert)

    except ValueError as e:
        console.print(f"[bold red]Validation Error for plan '{plan_upsert.name}': {e}[/bold red]")
        # Abort the entire transaction if any validation fails
        raise click.Abort()


def update_plan_helper(
    db_session: Session, existing_plan: Plan, new_plan_upsert: PlanCreate
):
    """Handles the update of an existing plan and displays a diff."""
    # To properly diff, we need to convert the existing ORM object to our upsert schema
    existing_plan_data = {
        "name": existing_plan.name,
        "duration": existing_plan.duration,
        "features": existing_plan.features,
        "price": existing_plan.price,
        "revenue_cat_product_id": existing_plan.revenue_cat_product_id,
        "automation_runs_limit": existing_plan.automation_runs_limit,
        "voice_chat_minutes_limit": existing_plan.voice_chat_minutes_limit,
        "automations_limit": existing_plan.automations_limit,
        "description": existing_plan.description,
        "trial_days": existing_plan.trial_days,
        "apps_limit": existing_plan.apps_limit,
        "active": existing_plan.active,
        "is_popular": existing_plan.is_popular,
        "display_order": existing_plan.display_order,
    }

    # Sort lists for consistent diffing
    existing_plan_data["features"] = sorted(existing_plan_data["features"])

    diff = DeepDiff(
        existing_plan_data,
        new_plan_upsert.model_dump(),
        ignore_order=True,
    )

    if not diff:
        console.rule(f"Plan '{existing_plan.name}' is up to date.")
        return

    console.rule(f"Updating plan '{existing_plan.name}' with the following changes:")
    console.print(diff.pretty())

    crud.plans.update_plan(
        db_session, existing_plan, **new_plan_upsert.model_dump(exclude_unset=True)
    )