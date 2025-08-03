import click
import json
from rich.console import Console
from rich.table import Table
from pydantic import ValidationError

from aci.cli import config
from aci.common import utils
from aci.common.db import crud
from aci.common.enums import SecurityScheme
from aci.common.schemas.app_configurations import (
    AppConfigurationCreate,
    AppConfigurationUpdate,
)
from aci.common.schemas.security_scheme import SecuritySchemeOverrides


console = Console()


@click.group()
def app_config():
    """
    Manage App Configurations in the database.
    
    This CLI interacts with app configurations, which control authentication,
    authorization, and function enablement for each app.
    """
    pass


@app_config.command("create")
@click.option(
    "--app-name",
    required=True,
    help="The name of the app to configure.",
)
@click.option(
    "--security-scheme",
    required=True,
    type=click.Choice([s.value for s in SecurityScheme], case_sensitive=False),
    help="The security scheme to use for the app.",
)
@click.option(
    "--overrides-json",
    "security_scheme_overrides_json",
    help="JSON string for security scheme overrides (e.g., client_id, scopes).",
)
@click.option(
    "--disable-all-functions",
    is_flag=True,
    help="Use this flag to disable all functions by default. Use --enabled-functions to specify exceptions.",
)
@click.option(
    "--enabled-functions",
    "enabled_functions_str",
    help="Comma-separated list of functions to enable. Only used if --disable-all-functions is set.",
)
@click.option(
    "--skip-dry-run",
    is_flag=True,
    help="Provide this flag to apply changes to the database.",
)
def create_config(
    app_name: str,
    security_scheme: str,
    security_scheme_overrides_json: str,
    disable_all_functions: bool,
    enabled_functions_str: str,
    skip_dry_run: bool,
):
    """
    Create a new app configuration.
    
    Example for OAuth2:
    
    --overrides-json '{"oauth2": {"client_id": "my-id", "client_secret": "my-secret", "scopes": ["read", "write"]}}'
    """
    overrides_dict = {}
    if security_scheme_overrides_json:
        try:
            overrides_dict = json.loads(security_scheme_overrides_json)
        except json.JSONDecodeError:
            raise click.ClickException("Invalid JSON format for --overrides-json.")

    all_functions_enabled = not disable_all_functions
    enabled_functions = enabled_functions_str.split(',') if enabled_functions_str else []

    try:
        config_data = AppConfigurationCreate(
            app_name=app_name,
            security_scheme=SecurityScheme(security_scheme),
            security_scheme_overrides=SecuritySchemeOverrides(**overrides_dict),
            all_functions_enabled=all_functions_enabled,
            enabled_functions=enabled_functions,
        )
    except (ValidationError, ValueError) as e:
        raise click.ClickException(f"Invalid configuration: {e}")

    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        # 1. Validate app exists
        app = crud.apps.get_app(db_session, app_name, active_only=True)
        if not app:
            raise click.ClickException(f"Active app '{app_name}' not found.")

        # 2. Validate app configuration doesn't already exist
        if crud.app_configurations.app_configuration_exists(db_session, app_name):
            raise click.ClickException(f"App configuration for '{app_name}' already exists.")

        console.print("[bold]Configuration to be created:[/bold]")
        console.print_json(data=config_data.model_dump(mode="json"))

        if skip_dry_run:
            try:
                crud.app_configurations.create_app_configuration(db_session, config_data)
                db_session.commit()
                console.rule(f"[bold green]Successfully created app configuration for '{app_name}'[/bold green]")
            except Exception as e:
                db_session.rollback()
                console.print(f"[bold red]Error creating app configuration: {e}[/bold red]")
                raise click.Abort()
        else:
            console.rule("[bold yellow]Dry run mode - no changes applied[/bold yellow]")
            console.print("Run with [bold green]--skip-dry-run[/bold green] to apply these changes.")


@app_config.command("list")
@click.option(
    "--app-name", "app_names",
    multiple=True,
    help="Filter by one or more app names. Can be used multiple times."
)
@click.option("--limit", type=int, default=100, help="Number of configurations to return.")
@click.option("--offset", type=int, default=0, help="Offset for pagination.")
def list_configs(app_names: list[str], limit: int, offset: int):
    """List all app configurations."""
    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        configs = crud.app_configurations.get_app_configurations(db_session, app_names, limit, offset)

        if not configs:
            console.print("[yellow]No app configurations found.[/yellow]")
            return

        table = Table(title="App Configurations")
        table.add_column("App Name", style="cyan", no_wrap=True)
        table.add_column("Enabled", style="green")
        table.add_column("Security Scheme", style="magenta")
        table.add_column("All Functions", style="yellow")
        table.add_column("Updated At", style="blue")

        for cfg in configs:
            table.add_row(
                cfg.app_name,
                "✅" if cfg.enabled else "❌",
                cfg.security_scheme,
                "✅" if cfg.all_functions_enabled else "❌",
                str(cfg.updated_at.date()),
            )
        
        console.print(table)


@app_config.command("get")
@click.argument("app_name")
def get_config(app_name: str):
    """Get details for a specific app configuration."""
    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        app_cfg = crud.app_configurations.get_app_configuration(db_session, app_name)
        if not app_cfg:
            raise click.ClickException(f"App configuration for '{app_name}' not found.")

        # Correctly use the enum member for dictionary access
        display_overrides = app_cfg.security_scheme_overrides.copy() # Use a copy to avoid mutating the live object
        oauth_overrides = display_overrides.get(SecurityScheme.OAUTH2)
        
        # Scrub secret for display
        if oauth_overrides and oauth_overrides.get("client_secret"):
            oauth_overrides["client_secret"] = "******"
            
        console.rule(f"[bold]Configuration for {app_name}[/bold]")
        console.print(f"[bold cyan]App Name:[/bold cyan] {app_cfg.app_name}")
        console.print(f"[bold cyan]Enabled:[/bold cyan] {'✅' if app_cfg.enabled else '❌'}")
        console.print(f"[bold cyan]Security Scheme:[/bold cyan] {app_cfg.security_scheme.value}")
        console.print(f"[bold cyan]All Functions Enabled:[/bold cyan] {'✅' if app_cfg.all_functions_enabled else '❌'}")
        console.print(f"[bold cyan]Enabled Functions:[/bold cyan] {app_cfg.enabled_functions or 'None'}")
        console.print("[bold cyan]Security Overrides (Scrubbed):[/bold cyan]")
        console.print_json(data=display_overrides)


@app_config.command("update")
@click.argument("app_name")
@click.option("--enable/--disable", "enabled", default=None, help="Enable or disable the entire configuration.")
@click.option("--enable-all-functions/--disable-all-functions", "all_functions_enabled", default=None, help="Enable or disable all functions.")
@click.option("--set-enabled-functions", help="Set a new comma-separated list of enabled functions. Implies --disable-all-functions.")
@click.option(
    "--skip-dry-run",
    is_flag=True,
    help="Provide this flag to apply changes to the database.",
)
def update_config(app_name: str, enabled: bool, all_functions_enabled: bool, set_enabled_functions: str, skip_dry_run: bool):
    """Update an existing app configuration."""
    update_dict = {}
    if enabled is not None:
        update_dict["enabled"] = enabled
    if all_functions_enabled is not None:
        update_dict["all_functions_enabled"] = all_functions_enabled
    if set_enabled_functions is not None:
        update_dict["enabled_functions"] = set_enabled_functions.split(',')
        # As per schema, setting enabled_functions implies all_functions_enabled is False
        if all_functions_enabled is True:
             raise click.ClickException("--enable-all-functions cannot be used with --set-enabled-functions.")
        update_dict["all_functions_enabled"] = False

    if not update_dict:
        raise click.ClickException("No update options provided. Use --help for details.")

    try:
        update_data = AppConfigurationUpdate(**update_dict)
    except (ValidationError, ValueError) as e:
        raise click.ClickException(f"Invalid update: {e}")

    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        app_cfg = crud.app_configurations.get_app_configuration(db_session, app_name)
        if not app_cfg:
            raise click.ClickException(f"App configuration for '{app_name}' not found.")

        console.print(f"[bold]Updating configuration for {app_name}:[/bold]")
        console.print_json(data=update_data.model_dump(exclude_unset=True))

        if skip_dry_run:
            try:
                crud.app_configurations.update_app_configuration(db_session, app_cfg, update_data)
                db_session.commit()
                console.rule(f"[bold green]Successfully updated app configuration for '{app_name}'[/bold green]")
            except Exception as e:
                db_session.rollback()
                console.print(f"[bold red]Error updating app configuration: {e}[/bold red]")
                raise click.Abort()
        else:
            console.rule("[bold yellow]Dry run mode - no changes applied[/bold yellow]")
            console.print("Run with [bold green]--skip-dry-run[/bold green] to apply these changes.")


@app_config.command("delete")
@click.argument("app_name")
@click.option(
    "--skip-dry-run",
    is_flag=True,
    help="Provide this flag to apply changes to the database.",
)
def delete_config(app_name: str, skip_dry_run: bool):
    """Delete an app configuration and its associated linked accounts."""
    if skip_dry_run:
        console.print("[bold red]WARNING: This will permanently delete the app configuration and all associated linked accounts.[/bold red]")
        if not click.confirm("Are you sure you want to continue?", default=False):
            raise click.Abort()

    with utils.create_db_session(config.DB_FULL_URL) as db_session:
        app_cfg = crud.app_configurations.get_app_configuration(db_session, app_name)
        if not app_cfg:
            raise click.ClickException(f"App configuration for '{app_name}' not found.")
        
        linked_accounts = crud.linked_accounts.get_linked_accounts_by_app_id(db_session, app_cfg.app_id)

        console.print(f"App configuration for '[bold cyan]{app_name}[/bold cyan]' will be deleted.")
        if linked_accounts:
            console.print(f"[yellow]{len(linked_accounts)} associated linked account(s) will also be deleted.[/yellow]")

        if skip_dry_run:
            try:
                crud.linked_accounts.delete_linked_accounts_by_app_name(db_session, app_name)
                console.print(f"Deleted {len(linked_accounts)} linked account(s).")
                
                crud.app_configurations.delete_app_configuration(db_session, app_name)
                console.print(f"Deleted app configuration '{app_name}'.")
                
                db_session.commit()
                console.rule(f"[bold green]Successfully deleted app configuration for '{app_name}'[/bold green]")
            except Exception as e:
                db_session.rollback()
                console.print(f"[bold red]Error deleting app configuration: {e}[/bold red]")
                raise click.Abort()
        else:
            console.rule("[bold yellow]Dry run mode - no changes applied[/bold yellow]")
            console.print("Run with [bold green]--skip-dry-run[/bold green] to apply these changes.")