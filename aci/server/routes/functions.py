import json
from datetime import UTC, datetime
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from aci.common.db import crud
from aci.common.db.sql_models import Function
from aci.common.logging_setup import get_logger
from aci.common.schemas.function import (
    FunctionDetails,
    FunctionExecute,
    FunctionExecutionResult,
    FunctionsList,
)
from aci.server import config, utils
from aci.server import dependencies as deps
from aci.server.function_executors.function_utils import execute_function

router = APIRouter()
logger = get_logger(__name__)


@router.get("", response_model=list[FunctionDetails])
def list_functions(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    query_params: Annotated[FunctionsList, Query()],
) -> list[Function]:
    """Get a list of functions and their details. Sorted by function name."""
    return crud.functions.get_functions(
        context.db_session,
        True,
        query_params.app_names,
        query_params.limit,
        query_params.offset,
    )


@router.post(
    "/{function_name}/execute",
    response_model=FunctionExecutionResult,
    response_model_exclude_none=True,
)
def execute(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    function_name: str,
    body: FunctionExecute,
) -> FunctionExecutionResult:
    start_time = datetime.now(UTC)

    result = execute_function(
        db_session=context.db_session,
        user_id=context.user.id,
        function_name=function_name,
        function_input=body.function_input,
    )

    end_time = datetime.now(UTC)

    # TODO: reconsider the implementation handling large log fields
    try:
        execute_result_data = utils.truncate_if_too_large(
            json.dumps(result.data, default=str), config.MAX_LOG_FIELD_SIZE
        )
    except Exception:
        logger.exception("Failed to dump execute_result_data")
        execute_result_data = "failed to dump execute_result_data"

    try:
        function_input_data = utils.truncate_if_too_large(
            json.dumps(body.function_input, default=str), config.MAX_LOG_FIELD_SIZE
        )
    except Exception:
        logger.exception("Failed to dump function_input_data")
        function_input_data = "failed to dump function_input_data"

    logger.info(
        "function execution result",
        extra={
            "function_execution": {
                "app_name": (
                    function_name.split("__")[0] if "__" in function_name else "unknown"
                ),
                "function_name": function_name,
                "function_execution_start_time": start_time,
                "function_execution_end_time": end_time,
                "function_execution_duration": (end_time - start_time).total_seconds(),
                "function_input": function_input_data,
                "function_execution_result_success": result.success,
                "function_execution_result_error": result.error,
                "function_execution_result_data": execute_result_data,
                "function_execution_result_data_size": len(execute_result_data),
            }
        },
    )
    return result
