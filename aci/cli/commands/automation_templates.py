import json
from pathlib import Path

import click
from deepdiff import DeepDiff
from rich.console import Console
from sqlalchemy.orm import Session

from aci.cli import config
from aci.common import utils
from aci.common.db import crud
from aci.common.db.sql_models import AutomationTemplate
from aci.common.schemas.automation_templates import AutomationTemplateUpsert, TemplatesFile

console = Console()


@click.command()
@click.option(
    "--templates-file",
    "templates_file",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the automation templates definition JSON file.",
)
def upsert_templates(templates_file: Path):
    """
    Syncs Automation Templates in the DB from a JSON file.

    - Creates templates from the file that are not in the DB.
    - Updates templates in the DB that have changed in the file.
    - Deletes templates from the DB that are not present in the file.
    """
    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        try:
            with open(templates_file) as f:
                templates_data = json.load(f)
            
            # Validate the entire file structure first
            validated_file = TemplatesFile.model_validate(templates_data)
            templates_from_file = validated_file.templates

        except Exception as e:
            console.print(f"[bold red]Error loading or validating templates file: {e}[/bold red]")
            raise click.Abort()

        console.rule("Starting template sync process")

        # --- Sync Logic ---
        template_names_from_file = {t.name for t in templates_from_file}
        
        all_db_templates = crud.automation_templates.get_all_templates(db_session)
        template_names_from_db = {t.name for t in all_db_templates}

        # 1. Handle Deletions
        names_to_delete = template_names_from_db - template_names_from_file
        if names_to_delete:
            console.print("\n[bold yellow]Deleting templates not found in file:[/bold yellow]")
            for name in sorted(list(names_to_delete)):
                crud.automation_templates.delete_template_by_name(db_session, name)
                console.print(f"  - Deleting template '{name}'.")
        
        # 2. Handle Upserts
        console.print("\n[bold]Processing templates from file:[/bold]")
        for template_upsert in templates_from_file:
            upsert_template_helper(db_session, template_upsert)

        try:
            db_session.commit()
            console.rule(f"[bold green]Successfully synced all automation templates.[/bold green]")
        except Exception as e:
            console.print(f"[bold red]An error occurred during commit: {e}[/bold red]")
            db_session.rollback()
            raise click.Abort()


def upsert_template_helper(db_session: Session, template_upsert: AutomationTemplateUpsert):
    """Orchestrates the upsert logic for a single template."""
    try:
        existing_template = crud.automation_templates.get_template_by_name(
            db_session, template_upsert.name
        )

        if existing_template is None:
            console.rule(f"Creating new Template: {template_upsert.name}")
            crud.automation_templates.create_template(db_session, template_upsert)
            console.print(f"[green]  + Creating template '{template_upsert.name}'.[/green]")
        else:
            update_template_helper(db_session, existing_template, template_upsert)

    except ValueError as e:
        console.print(f"[bold red]Validation Error for template '{template_upsert.name}': {e}[/bold red]")
        # Abort the entire transaction if any validation fails
        raise click.Abort()


def update_template_helper(
    db_session: Session, existing_template: AutomationTemplate, new_template_upsert: AutomationTemplateUpsert
):
    """Handles the update of an existing template and displays a diff."""
    # To properly diff, we need to convert the existing ORM object to our upsert schema
    existing_template_data = {
        "name": existing_template.name,
        "description": existing_template.description,
        "banner_image_url": existing_template.banner_image_url,
        "tags": sorted(existing_template.tags),
        "goal": existing_template.goal,
        "variable_names": existing_template.variable_names,
        "is_deep": existing_template.is_deep,
        "required_app_names": sorted([app.name for app in existing_template.required_apps]),
    }
    existing_template_upsert = AutomationTemplateUpsert.model_validate(existing_template_data)
    
    # Sort lists in the new data for consistent diffing
    new_template_upsert.tags.sort()
    new_template_upsert.required_app_names.sort()

    diff = DeepDiff(
        existing_template_upsert.model_dump(),
        new_template_upsert.model_dump(),
        ignore_order=True,
    )

    if not diff:
        console.rule(f"Template '{existing_template.name}' is up to date.")
        return

    console.rule(f"Updating template '{existing_template.name}' with the following changes:")
    console.print(diff.pretty())

    crud.automation_templates.update_template(
        db_session, existing_template, new_template_upsert
    )
