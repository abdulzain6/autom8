from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from aci.common.schemas.activity import (
    ActivityFeed,
    ActivityStats,
    ActivityResponse,
    ActivityItem,
)
from aci.common.db.crud import activity as activity_crud
from aci.common.enums import RunStatus
from aci.server.dependencies import RequestContext, get_request_context


router = APIRouter()


@router.get("/", response_model=ActivityResponse)
def get_user_activity_feed(
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
    status: Optional[RunStatus] = Query(None, description="Filter by run status"),
    limit: int = Query(20, description="Number of activities to return", ge=1, le=100),
    offset: int = Query(0, description="Number of activities to skip", ge=0)
):
    """
    Get the user's activity feed showing recent automation runs.
    
    This endpoint returns a paginated list of the user's automation activities,
    including successful runs, failures, and currently running automations.
    """
    try:
        # Get the activities
        automation_runs = activity_crud.get_user_activity(
            db=context.db_session,
            user_id=context.user.id,
            limit=limit,
            offset=offset,
            status_filter=status
        )
        
        # Convert to ActivityItem objects
        activities = [
            ActivityItem.from_automation_run(run) 
            for run in automation_runs
        ]
        
        # Get total count for pagination
        total_count = activity_crud.get_user_activity_count(
            db=context.db_session,
            user_id=context.user.id,
            status_filter=status
        )
        
        # Calculate pagination info
        page = (offset // limit) + 1
        has_more = (offset + limit) < total_count
        
        activity_feed = ActivityFeed(
            activities=activities,
            total_count=total_count,
            page=page,
            page_size=limit,
            has_more=has_more
        )
        
        return ActivityResponse(
            success=True,
            data=activity_feed,
            message=f"Retrieved {len(activities)} activities"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve activity feed: {str(e)}"
        )


@router.get("/stats", response_model=ActivityResponse)
def get_user_activity_stats(context: RequestContext = Depends(get_request_context(check_subscription=False))):
    """
    Get activity statistics for the user.
    
    Returns aggregate statistics about the user's automation runs,
    including success/failure rates and recent activity counts.
    """
    try:
        stats_data = activity_crud.get_activity_stats(
            db=context.db_session,
            user_id=context.user.id
        )
        
        stats = ActivityStats(**stats_data)
        
        return ActivityResponse(
            success=True,
            stats=stats,
            message="Activity statistics retrieved successfully"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve activity statistics: {str(e)}"
        )


@router.get("/recent", response_model=ActivityResponse)
def get_recent_activity(
    context: RequestContext = Depends(get_request_context(check_subscription=False)),
    days: int = Query(7, description="Number of days to look back", ge=1, le=30)
):
    """
    Get recent activity for the specified number of days.
    
    Returns activities from the last N days, useful for showing
    recent automation history and trends.
    """
    try:
        automation_runs = activity_crud.get_recent_user_activity(
            db=context.db_session,
            user_id=context.user.id,
            days=days
        )
        
        activities = [
            ActivityItem.from_automation_run(run)
            for run in automation_runs
        ]
        
        activity_feed = ActivityFeed(
            activities=activities,
            total_count=len(activities),
            page=1,
            page_size=len(activities),
            has_more=False
        )
        
        return ActivityResponse(
            success=True,
            data=activity_feed,
            message=f"Retrieved {len(activities)} activities from the last {days} days"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve recent activity: {str(e)}"
        )


@router.get("/{activity_id}")
async def get_activity_details(
    activity_id: str,
    context: RequestContext = Depends(get_request_context(check_subscription=False))
):
    """
    Get detailed information about a specific activity.
    
    Returns full details about an automation run, including logs,
    artifacts, and execution details.
    """
    try:
        from aci.common.db.crud import automation_runs
        
        # Get the specific automation run
        automation_run = automation_runs.get_run(
            db=context.db_session,
            run_id=activity_id
        )
        
        if not automation_run:
            raise HTTPException(
                status_code=404,
                detail=f"Activity with ID {activity_id} not found"
            )
        
        # Verify the automation belongs to the user
        if automation_run.automation.user_id != context.user.id:
            raise HTTPException(
                status_code=403,
                detail="You don't have permission to view this activity"
            )
        
        # Convert to ActivityItem with full details
        activity_item = ActivityItem.from_automation_run(automation_run)
        
        # Add additional details for single item view
        activity_details = {
            **activity_item.model_dump(),
            "logs": automation_run.logs,
            "automation_description": automation_run.automation.description,
            "automation_goal": automation_run.automation.goal,
            "artifacts_count": len(automation_run.artifacts) if automation_run.artifacts else 0
        }
        
        return {
            "success": True,
            "data": activity_details,
            "message": "Activity details retrieved successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve activity details: {str(e)}"
        )
