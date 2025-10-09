from aci.server import config
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pythonjsonlogger.json import JsonFormatter
from starlette.middleware.cors import CORSMiddleware
from aci.common.exceptions import ACIException
from aci.server.file_management import FileManager
from aci.common.logging_setup import setup_logging
from aci.common.utils import create_db_session
from aci.server.log_schema_filter import LogSchemaFilter
from aci.server.routes import (
    apps,
    functions,
    health,
    voice_agent,
    linked_accounts,
    profile,
    automation_runs,
    automation_templates,
    automations,
    fcm_tokens,
    activity,
    usage,
    plans
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis import asyncio as aioredis
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events for the application.
    Initializes the Redis cache backend on startup.
    """
    try:
        scheduler.add_job(run_cleanup_job, "interval", hours=1)
        scheduler.start()
        logger.info("Scheduler started and cleanup job scheduled.")
        redis = await aioredis.from_url(config.REDIS_URL)
        await redis.ping()
        logger.info("Successfully connected to Redis for caching.")
        FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
    except Exception as e:
        logger.info(f"Could not connect to Redis: {e}")
        logger.info("Caching will be disabled.")
    
    yield  # The application runs while in this context
    
    logger.info("Application shutdown...")
    scheduler.shutdown()
    await FastAPICache.clear()


app = FastAPI(
    title=config.APP_TITLE,
    version=config.APP_VERSION,
    docs_url=config.APP_DOCS_URL,
    redoc_url=config.APP_REDOC_URL,
    openapi_url=config.APP_OPENAPI_URL,
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan
)
scheduler = AsyncIOScheduler()
logger = logging.getLogger(__name__)


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

async def run_cleanup_job():
    """
    A wrapper function for the cleanup task that handles its own DB session.
    """
    logger.info("Scheduler starting cleanup job...")
    db = create_db_session(config.DB_FULL_URL)
    try:
        manager = FileManager(db)
        deleted, failed = manager.cleanup_expired_artifacts()
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
app.include_router(
    profile.router,
    prefix=config.ROUTER_PREFIX_PROFILE,
    tags=[config.ROUTER_PREFIX_PROFILE.split("/")[-1]],
)
app.include_router(
    automations.router,
    prefix=config.ROUTER_PREFIX_AUTOMATIONS,
    tags=[config.ROUTER_PREFIX_AUTOMATIONS.split("/")[-1]],
)
app.include_router(
    automation_templates.router,
    prefix=config.ROUTER_PREFIX_AUTOMATION_TEMPLATES,
    tags=[config.ROUTER_PREFIX_AUTOMATION_TEMPLATES.split("/")[-1]],
)
app.include_router(
    automation_runs.router,
    prefix=config.ROUTER_PREFIX_AUTOMATION_RUNS,
    tags=[config.ROUTER_PREFIX_AUTOMATION_RUNS.split("/")[-1]],
)
app.include_router(
    fcm_tokens.router,
    prefix=config.ROUTER_PREFIX_FCM,
    tags=[config.ROUTER_PREFIX_FCM.split("/")[-1]],
)
app.include_router(
    activity.router,
    prefix=config.ROUTER_PREFIX_ACTIVITY,
    tags=[config.ROUTER_PREFIX_ACTIVITY.split("/")[-1]],
)
app.include_router(
    usage.router,
    prefix=config.ROUTER_PREFIX_USAGE,
    tags=[config.ROUTER_PREFIX_USAGE.split("/")[-1]],
)
app.include_router(
    plans.router,
    prefix=config.ROUTER_PREFIX_PLANS,
    tags=[config.ROUTER_PREFIX_PLANS.split("/")[-1]],
)