from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pythonjsonlogger.json import JsonFormatter
from starlette.middleware.cors import CORSMiddleware
from aci.common.exceptions import ACIException
from aci.common.logging_setup import setup_logging
from aci.server import config
from aci.server.log_schema_filter import LogSchemaFilter
from aci.server.middleware.ratelimit import RateLimitMiddleware
from aci.server.routes import (
    apps,
    functions,
    health,
    linked_accounts,
)



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


"""middlewares are executed in the reverse order"""
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
