import click

from aci.cli.commands import (
    delete_app,
    get_app,
    rename_app,
    upsert_app,
    upsert_functions,
    default_credentials,
    app_configs,
)
from aci.common.logging_setup import setup_logging


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """AIPO CLI Tool"""
    setup_logging()


# Add commands to the group
cli.add_command(upsert_app.upsert_app)
cli.add_command(get_app.get_app)
cli.add_command(rename_app.rename_app)
cli.add_command(delete_app.delete_app)
cli.add_command(upsert_functions.upsert_functions)
cli.add_command(default_credentials.set_credentials)
cli.add_command(default_credentials.delete_credentials)
cli.add_command(app_configs.create_config)
cli.add_command(app_configs.delete_config)
cli.add_command(app_configs.get_config)
cli.add_command(app_configs.update_config)

if __name__ == "__main__":
    cli()
