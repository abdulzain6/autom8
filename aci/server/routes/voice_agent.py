import json
import uuid
from aci.server import dependencies as deps
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from livekit import api
from aci.server.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_HOST_URL


lk_api = api.LiveKitAPI(LIVEKIT_HOST_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

router = APIRouter()


@router.post("/start-session")
async def start_session(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
):
    """
    Generates a user token AND dispatches a job for an agent using create_dispatch.
    """
    unique_id = uuid.uuid4().hex[:8] 
    room_name = f"voice-ai-session-{context.user.id}-{unique_id}"

    # 1. Generate a token for the human user
    video_grant = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish_data=True,
    )
    user_token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(context.user.id)
        .with_grants(video_grant)
        .to_jwt()
    )

    # 2. Prepare metadata for the agent
    agent_metadata = {
        "user_id": context.user.id,
    }

    # 3. Dispatch the job using the new, correct method names
    try:
        await lk_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                metadata=json.dumps(agent_metadata),
                agent_name="Autom8 AI"
            )
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not dispatch agent: {e}")

    # Return the user's token to the client application
    return {"user_token": user_token}