from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pythonjsonlogger.json import JsonFormatter
from starlette.middleware.cors import CORSMiddleware
from aci.common.exceptions import ACIException
from aci.server.file_management import FileManager
from aci.common.logging_setup import setup_logging
from aci.server import config
from aci.common.utils import create_db_session
from aci.server.log_schema_filter import LogSchemaFilter
from aci.server.middleware.ratelimit import RateLimitMiddleware
from aci.server.routes import (
    apps,
    functions,
    health,
    voice_agent,
    linked_accounts,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging


setup_logging(
    formatter=JsonFormatter(
        "{levelname} {asctime} {name} {message}",
        style="{",
        rename_fields={"asctime": "timestamp", "name": "file", "levelname": "level"},
    ),
    filters=[LogSchemaFilter()],
    environment=config.ENVIRONMENT,
)


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


app = FastAPI(
    title=config.APP_TITLE,
    version=config.APP_VERSION,
    docs_url=config.APP_DOCS_URL,
    redoc_url=config.APP_REDOC_URL,
    openapi_url=config.APP_OPENAPI_URL,
    generate_unique_id_function=custom_generate_unique_id,
)
scheduler = AsyncIOScheduler()
logger = logging.getLogger(__name__)


app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.exception_handler(ACIException)
async def global_exception_handler(request: Request, exc: ACIException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.error_code,
        content={"error": f"{exc.title}, {exc.message}" if exc.message else exc.title},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    scheduler.add_job(run_cleanup_job, "interval", hours=1)
    scheduler.start()
    logger.info("Scheduler started and cleanup job scheduled.")

    yield  # Application runs while inside this context

    # Shutdown
    scheduler.shutdown()
    logger.info("Scheduler shut down.")


async def run_cleanup_job():
    """
    A wrapper function for the cleanup task that handles its own DB session.
    """
    logger.info("Scheduler starting cleanup job...")
    db = create_db_session(config.DB_FULL_URL)
    try:
        manager = FileManager(db)
        deleted, failed = manager.delete_expired_files()
        logger.info(f"Cleanup job finished. Deleted: {deleted}, Failed: {failed}")
    except Exception as e:
        logger.error(f"Cleanup job failed with an exception: {e}")
    finally:
        db.close()


app.include_router(
    health.router,
    prefix=config.ROUTER_PREFIX_HEALTH,
    tags=[config.ROUTER_PREFIX_HEALTH.split("/")[-1]],
)
app.include_router(
    apps.router,
    prefix=config.ROUTER_PREFIX_APPS,
    tags=[config.ROUTER_PREFIX_APPS.split("/")[-1]],
)
app.include_router(
    functions.router,
    prefix=config.ROUTER_PREFIX_FUNCTIONS,
    tags=[config.ROUTER_PREFIX_FUNCTIONS.split("/")[-1]],
)
app.include_router(
    linked_accounts.router,
    prefix=config.ROUTER_PREFIX_LINKED_ACCOUNTS,
    tags=[config.ROUTER_PREFIX_LINKED_ACCOUNTS.split("/")[-1]],
)

app.include_router(
    voice_agent.router,
    prefix=config.ROUTER_PREFIX_VOICE_AGENT,
    tags=[config.ROUTER_PREFIX_VOICE_AGENT.split("/")[-1]],
)
