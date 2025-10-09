from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from aci.common.db import crud
from aci.common.enums import PlanDuration
from aci.common.logging_setup import get_logger
from aci.common.schemas.plan import PlanResponse, PlanList, PlanSearch
from aci.server import dependencies as deps

logger = get_logger(__name__)
router = APIRouter()


@router.get("/", response_model=PlanList)
@deps.typed_cache(expire=300)  # Cache for 5 minutes since plans change infrequently
def get_all_plans(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    active_only: Annotated[bool, Query(description="Only return active plans")] = True,
    popular_only: Annotated[bool, Query(description="Only return popular plans")] = False,
) -> PlanList:
    """
    Get all plans with optional filtering.
    """
    try:
        if popular_only:
            plans = crud.plans.get_popular_plans(
                db_session=context.db_session,
                active_only=active_only
            )
        else:
            plans = crud.plans.get_all_plans(
                db_session=context.db_session,
                active_only=active_only
            )

        logger.info(
            f"Retrieved {len(plans)} plans",
            extra={
                "active_only": active_only,
                "popular_only": popular_only,
                "total_plans": len(plans)
            }
        )

        plan_responses = [
            PlanResponse.model_validate(plan, from_attributes=True) for plan in plans
        ]

        return PlanList(plans=plan_responses, total=len(plan_responses))

    except Exception as e:
        logger.error(f"Failed to retrieve plans: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve plans")


@router.get("/{plan_id}", response_model=PlanResponse)
@deps.typed_cache(expire=300)  # Cache for 5 minutes
def get_plan_by_id(
    plan_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
) -> PlanResponse:
    """
    Get a specific plan by ID.
    """
    try:
        plan = crud.plans.get_plan_by_id(db_session=context.db_session, plan_id=plan_id)

        if not plan:
            raise HTTPException(status_code=404, detail=f"Plan with ID '{plan_id}' not found")

        logger.info(
            f"Retrieved plan: {plan.name}",
            extra={"plan_id": plan_id, "plan_name": plan.name}
        )

        return PlanResponse.model_validate(plan, from_attributes=True)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve plan {plan_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve plan")


@router.get("/revenue-cat/{revenue_cat_id}", response_model=PlanResponse)
@deps.typed_cache(expire=300)  # Cache for 5 minutes
def get_plan_by_revenue_cat_id(
    revenue_cat_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
) -> PlanResponse:
    """
    Get a plan by RevenueCat product ID.
    """
    try:
        plan = crud.plans.get_plan_by_revenue_cat_id(
            db_session=context.db_session,
            revenue_cat_product_id=revenue_cat_id
        )

        if not plan:
            raise HTTPException(
                status_code=404,
                detail=f"Plan with RevenueCat ID '{revenue_cat_id}' not found"
            )

        logger.info(
            f"Retrieved plan by RevenueCat ID: {plan.name}",
            extra={"revenue_cat_id": revenue_cat_id, "plan_name": plan.name}
        )

        return PlanResponse.model_validate(plan, from_attributes=True)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve plan by RevenueCat ID {revenue_cat_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve plan")


@router.post("/search", response_model=PlanList)
def search_plans(
    search_params: PlanSearch,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
) -> PlanList:
    """
    Search plans by name, description, or filter by duration.
    """
    try:
        plans = crud.plans.search_plans(
            db_session=context.db_session,
            search_term=search_params.query,
            duration=search_params.duration,
            active_only=search_params.active_only,
            limit=search_params.limit
        )

        logger.info(
            f"Search returned {len(plans)} plans",
            extra={
                "query": search_params.query,
                "duration": search_params.duration,
                "active_only": search_params.active_only,
                "limit": search_params.limit,
                "results_count": len(plans)
            }
        )

        plan_responses = [
            PlanResponse.model_validate(plan, from_attributes=True) for plan in plans
        ]

        return PlanList(plans=plan_responses, total=len(plan_responses))

    except Exception as e:
        logger.error(f"Failed to search plans: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to search plans")


@router.get("/duration/{duration}", response_model=PlanList)
@deps.typed_cache(expire=300)  # Cache for 5 minutes
def get_plans_by_duration(
    duration: PlanDuration,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    active_only: Annotated[bool, Query(description="Only return active plans")] = True,
) -> PlanList:
    """
    Get all plans with a specific duration.
    """
    try:
        plans = crud.plans.get_plans_by_duration(
            db_session=context.db_session,
            duration=duration,
            active_only=active_only
        )

        logger.info(
            f"Retrieved {len(plans)} plans for duration: {duration}",
            extra={
                "duration": duration,
                "active_only": active_only,
                "total_plans": len(plans)
            }
        )

        plan_responses = [
            PlanResponse.model_validate(plan, from_attributes=True) for plan in plans
        ]

        return PlanList(plans=plan_responses, total=len(plan_responses))

    except Exception as e:
        logger.error(f"Failed to retrieve plans for duration {duration}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve plans")


@router.get("/popular", response_model=PlanList)
@deps.typed_cache(expire=300)  # Cache for 5 minutes
def get_popular_plans(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    active_only: Annotated[bool, Query(description="Only return active plans")] = True,
) -> PlanList:
    """
    Get all popular plans.
    """
    try:
        plans = crud.plans.get_popular_plans(
            db_session=context.db_session,
            active_only=active_only
        )

        logger.info(
            f"Retrieved {len(plans)} popular plans",
            extra={
                "active_only": active_only,
                "total_plans": len(plans)
            }
        )

        plan_responses = [
            PlanResponse.model_validate(plan, from_attributes=True) for plan in plans
        ]

        return PlanList(plans=plan_responses, total=len(plan_responses))

    except Exception as e:
        logger.error(f"Failed to retrieve popular plans: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve popular plans")