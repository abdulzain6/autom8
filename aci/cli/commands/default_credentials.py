import json
import click
from rich.console import Console
from aci.cli import config
from aci.common import utils
from aci.common.db import crud
from aci.common.enums import SecurityScheme
from aci.common.schemas.app import DefaultAppCredentialCreate
from pydantic import ValidationError

console = Console()


@click.group("default-credentials")
def default_credentials_cli():
    """
    Manage Default Credentials for Apps.
    """
    pass


@default_credentials_cli.command("set")
@click.option("--app-name", required=True, help="The name of the app to set credentials for.")
@click.option(
    "--security-scheme",
    required=True,
    type=click.Choice([s.value for s in SecurityScheme], case_sensitive=False),
    help="The security scheme for these credentials.",
)
@click.option(
    "--credentials-json",
    "credentials_json_str",
    required=True,
    help='JSON string for the credentials. E.g., \'{"api_key": "my-secret-key"}\'',
)
@click.option(
    "--skip-dry-run",
    is_flag=True,
    help="Provide this flag to apply changes to the database.",
)
def set_credentials(
    app_name: str, security_scheme: str, credentials_json_str: str, skip_dry_run: bool
):
    """
    Set or update the default credentials for an app.

    If credentials already exist, they will be overwritten.
    """
    try:
        credentials_dict = json.loads(credentials_json_str)
        credential_data = DefaultAppCredentialCreate(
            security_scheme=SecurityScheme(security_scheme),
            credentials=credentials_dict,
        )
    except (json.JSONDecodeError, ValidationError) as e:
        raise click.ClickException(f"Invalid credentials JSON or schema: {e}")

    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        # 1. Check if the app exists
        app = crud.apps.get_app(db_session, app_name, active_only=False)
        if not app:
            raise click.ClickException(f"App '{app_name}' not found.")

        # 2. Check if credentials already exist for this app
        existing_creds = crud.default_credentials.get_default_app_credential_by_app_id(
            db_session, app.id
        )

        console.rule(f"Setting default credentials for App: {app_name}")
        if existing_creds:
            console.print("[yellow]Existing credentials found and will be replaced.[/yellow]")

        console.print("[bold]Credentials to be set:[/bold]")
        console.print_json(data=credential_data.model_dump(mode="json"))

        if not skip_dry_run:
            console.rule("[bold yellow]Dry run mode - no changes applied.[/bold yellow]")
            console.print("Run with [bold green]--skip-dry-run[/bold green] to apply these changes.")
            db_session.rollback()
            return

        try:
            # If creds exist, delete them first to ensure a clean update
            if existing_creds:
                crud.default_credentials.delete_default_app_credential_by_app_id(
                    db_session, app.id
                )
            # Create the new credentials
            crud.default_credentials.create_default_app_credential(
                db_session, app.id, credential_data
            )
            db_session.commit()
            console.rule(
                f"[bold green]Successfully set default credentials for '{app_name}'[/bold green]"
            )
        except Exception as e:
            db_session.rollback()
            console.print(f"[bold red]Error setting credentials: {e}[/bold red]")
            raise click.Abort()


@default_credentials_cli.command("delete")
@click.option("--app-name", required=True, help="The name of the app to delete credentials from.")
@click.option(
    "--skip-dry-run",
    is_flag=True,
    help="Provide this flag to apply changes to the database.",
)
def delete_credentials(app_name: str, skip_dry_run: bool):
    """
    Delete the default credentials for an app.
    """
    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        # 1. Check if the app exists
        app = crud.apps.get_app(db_session, app_name, active_only=False)
        if not app:
            raise click.ClickException(f"App '{app_name}' not found.")

        # 2. Check if credentials exist for this app
        existing_creds = crud.default_credentials.get_default_app_credential_by_app_id(
            db_session, app.id
        )
        if not existing_creds:
            console.print(f"[yellow]No default credentials found for app '{app_name}'.[/yellow]")
            return

        console.rule(f"Deleting default credentials for App: {app_name}")

        if not skip_dry_run:
            console.rule("[bold yellow]Dry run mode - no changes applied.[/bold yellow]")
            console.print("Run with [bold green]--skip-dry-run[/bold green] to apply this change.")
            db_session.rollback()
            return

        try:
            crud.default_credentials.delete_default_app_credential_by_app_id(
                db_session, app.id
            )
            db_session.commit()
            console.rule(
                f"[bold green]Successfully deleted default credentials for '{app_name}'[/bold green]"
            )
        except Exception as e:
            db_session.rollback()
            console.print(f"[bold red]Error deleting credentials: {e}[/bold red]")
            raise click.Abort()

if __name__ == "__main__":
    default_credentials_cli()
