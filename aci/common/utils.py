import os
import re
from functools import cache
from uuid import UUID
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from aci.common.logging_setup import get_logger
from typing import Optional
from langchain_xai import ChatXAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from pydantic import SecretStr



logger = get_logger(__name__)


def check_and_get_env_variable(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"Environment variable '{name}' is not set")
    if value == "":
        raise ValueError(f"Environment variable '{name}' is empty string")
    return value


def construct_db_url(
    scheme: str, user: str, password: str, host: str, port: str, db_name: str
) -> str:
    return f"{scheme}://{user}:{password}@{host}:{port}/{db_name}"


def format_to_screaming_snake_case(name: str) -> str:
    """
    Convert a string with spaces, hyphens, slashes, camel case etc. to screaming snake case.
    e.g., "GitHub Create Repository" -> "GITHUB_CREATE_REPOSITORY"
    e.g., "GitHub/Create Repository" -> "GITHUB_CREATE_REPOSITORY"
    e.g., "github-create-repository" -> "GITHUB_CREATE_REPOSITORY"
    """
    name = re.sub(
        r"[\W]+", "_", name
    )  # Replace non-alphanumeric characters with underscore
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
    s3 = s2.replace("-", "_").replace("/", "_").replace(" ", "_")
    s3 = re.sub("_+", "_", s3)  # Replace multiple underscores with single underscore
    s4 = s3.upper().strip("_")

    return s4


# NOTE: it's important that you don't create a new engine for each session, which takes
# up db resources and will lead up to errors pretty fast
# TODO: fine tune the pool settings
@cache
def get_db_engine(db_url: str) -> Engine:
    engine = create_engine(
        db_url,
        pool_size=10,
        max_overflow=10,
        pool_timeout=120,  # Increased from 30 to 120 seconds for long-running operations
        pool_recycle=1800,  # Reduced from 3600 to 1800 seconds (30 minutes) for better connection health
        pool_pre_ping=True,  # Enable pre-ping to detect dead connections
        connect_args={
            "prepare_threshold": None,  # Disable prepared statements
            "autocommit": False,  # Explicit autocommit setting
            "connect_timeout": 60,  # Connection timeout
        },
        isolation_level="READ_COMMITTED",  # Set explicit isolation level
        echo=False,  # Set to True for debugging SQL queries
    )

    # Add event listeners for better connection management
    @event.listens_for(engine, "connect")
    def set_autocommit_false(dbapi_connection, connection_record):
        """Ensure autocommit is disabled on connection."""
        dbapi_connection.autocommit = False

    @event.listens_for(engine, "checkout")
    def on_checkout(dbapi_connection, connection_record, connection_proxy):
        """Called when a connection is retrieved from the pool."""
        pass  # Can add additional connection validation here if needed

    @event.listens_for(engine, "checkin")
    def on_checkin(dbapi_connection, connection_record):
        """Called when a connection is returned to the pool."""
        pass  # Can add connection cleanup here if needed

    return engine


def create_db_session(db_url: str) -> Session:
    SessionMaker = get_sessionmaker(db_url)
    session: Session = SessionMaker()
    return session


@cache
def get_sessionmaker(db_url: str) -> sessionmaker:
    engine = get_db_engine(db_url)
    return sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )


def parse_app_name_from_function_name(function_name: str) -> str:
    """
    Parse the app name from a function name.
    e.g., "ACI_TEST__HELLO_WORLD" -> "ACI_TEST"
    """
    return function_name.split("__")[0]


def snake_to_camel(string: str) -> str:
    """
    Convert a snake case string to a camel case string.
    e.g., "snake_case_string" -> "SnakeCaseString"
    """
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


def is_uuid(value: str | UUID) -> bool:
    if isinstance(value, UUID):
        return True
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def generate_automation_description(
    name: str,
    goal: str,
    app_names: list[str],
) -> Optional[str]:
    """
    Generate a concise one-line description for an automation using LLM.

    Args:
        name: The automation name
        goal: The automation goal/instruction
        app_names: List of app names used in the automation
    
    Returns:
        Generated description string or None if generation fails
    """
    try:        
        from aci.server.config import XAI_API_KEY
        # Initialize the LLM
        llm = ChatXAI(
            api_key=SecretStr(XAI_API_KEY),
            model="grok-4-1-fast-non-reasoning-latest",
            timeout=300,
            max_retries=3,
        )

        # Create the prompt template
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful assistant that creates concise, professional descriptions for automation workflows. Generate a single sentence description that explains what the automation does in simple terms.",
                ),
                (
                    "human",
                    """Create a one-line description for this automation:

Name: {name}
Goal: {goal}
Apps used: {apps}

Generate a clear, concise description (max 100 characters) that explains what this automation accomplishes.""",
                ),
            ]
        )

        # Create the chain
        chain = prompt | llm | StrOutputParser()

        # Generate the description
        description = chain.invoke(
            {"name": name, "goal": goal, "apps": ", ".join(app_names)}
        )

        # Clean up the description (remove quotes, trim whitespace)
        description = description.strip().strip('"').strip("'")

        # Ensure it's not too long
        if len(description) > 200:
            description = description[:197] + "..."

        logger.info(f"Generated description for automation '{name}': {description}")
        return description

    except Exception as e:
        logger.error(
            f"Failed to generate description for automation '{name}': {str(e)}"
        )
        return None


def _clear_engine_cache():
    """
    Clears the DB engine and sessionmaker caches.
    This is registered to run after a fork in the child process to ensure
    that each process gets its own DB engine and connection pool.
    """
    get_db_engine.cache_clear()
    get_sessionmaker.cache_clear()
    logger.info("Cleared DB engine cache after fork.")


# Register the hook to ensure fork safety for multiprocessing (FastAPI workers, LiveKit agents)
os.register_at_fork(after_in_child=_clear_engine_cache)
