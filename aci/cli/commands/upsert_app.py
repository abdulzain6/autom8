import json
from pathlib import Path


import click
from deepdiff import DeepDiff
from openai import OpenAI
from rich.console import Console
from sqlalchemy.orm import Session

from aci.cli import config
from aci.common import embeddings, utils
from aci.common.db import crud
from aci.common.db.sql_models import App
from aci.common.schemas.app import AppEmbeddingFields, AppUpsert


console = Console()

openai_client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)


@click.command()
@click.option(
    "--app-file",
    "app_file",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the app definition JSON file.",
)
@click.option(
    "--skip-dry-run",
    is_flag=True,
    help="Provide this flag to run the command and apply changes to the database.",
)
def upsert_app(app_file: Path, skip_dry_run: bool) -> str:
    """
    Insert or update an App in the DB from a JSON file.

    If an app with the given name already exists, it performs an update;
    otherwise, it creates a new app.
    """
    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        return upsert_app_helper(db_session, app_file, skip_dry_run)


def upsert_app_helper(
    db_session: Session, app_file: Path, skip_dry_run: bool
) -> str:
    """Helper function to orchestrate the app upsert logic."""
    try:
        with open(app_file) as f:
            app_data = json.load(f)
        app_upsert = AppUpsert.model_validate(app_data)
    except Exception as e:
        console.print(f"[bold red]Error loading or validating app file: {e}[/bold red]")
        raise e

    existing_app = crud.apps.get_app(db_session, app_upsert.name, active_only=False)

    if existing_app is None:
        return create_app_helper(db_session, app_upsert, skip_dry_run)
    else:
        return update_app_helper(db_session, existing_app, app_upsert, skip_dry_run)


def create_app_helper(db_session: Session, app_upsert: AppUpsert, skip_dry_run: bool) -> str:
    """Handles the creation of a new app."""
    console.rule(f"Creating new App: {app_upsert.name}")

    # Generate app embedding
    app_embedding = embeddings.generate_app_embedding(
        AppEmbeddingFields.model_validate(app_upsert.model_dump()),
        openai_client,
        config.OPENAI_EMBEDDING_MODEL,
        config.OPENAI_EMBEDDING_DIMENSION,
    )

    # Create the app entry in the database
    app = crud.apps.create_app(db_session, app_upsert, app_embedding)

    if not skip_dry_run:
        console.rule(f"Provide [bold green]--skip-dry-run[/bold green] to create App '{app.name}'")
        db_session.rollback()
    else:
        db_session.commit()
        console.rule(f"[bold green]Successfully created App '{app.name}'[/bold green]")

    return app.id


def update_app_helper(
    db_session: Session, existing_app: App, new_app_upsert: AppUpsert, skip_dry_run: bool
) -> str:
    """Handles the update of an existing app."""
    existing_app_upsert = AppUpsert.model_validate(existing_app, from_attributes=True)

    diff = DeepDiff(existing_app_upsert.model_dump(), new_app_upsert.model_dump(), ignore_order=True)
    if not diff:
        console.rule(f"App '{existing_app.name}' exists and is up to date.")
        return existing_app.id

    console.rule(f"App '{existing_app.name}' exists and will be updated with the following changes:")
    console.print(diff.pretty())

    # Determine if embedding needs regeneration
    if _need_embedding_regeneration(existing_app_upsert, new_app_upsert):
        console.print("  - App metadata changed, regenerating embedding...")
        new_embedding = embeddings.generate_app_embedding(
            AppEmbeddingFields.model_validate(new_app_upsert.model_dump()),
            openai_client,
            config.OPENAI_EMBEDDING_MODEL,
            config.OPENAI_EMBEDDING_DIMENSION,
        )
    else:
        new_embedding = None

    # Update the core app fields
    crud.apps.update_app(db_session, existing_app, new_app_upsert, new_embedding)

    if not skip_dry_run:
        console.rule(f"Provide [bold green]--skip-dry-run[/bold green] to apply updates.")
        db_session.rollback()
    else:
        db_session.commit()
        console.rule(f"[bold green]Successfully updated App '{existing_app.name}'[/bold green]")

    return existing_app.id


def _need_embedding_regeneration(old_app: AppUpsert, new_app: AppUpsert) -> bool:
    """Checks if fields affecting the embedding have changed."""
    fields = set(AppEmbeddingFields.model_fields.keys())
    return bool(old_app.model_dump(include=fields) != new_app.model_dump(include=fields))
