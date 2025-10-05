# aci/server/routers/profile.py
from typing import Annotated
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from fastapi.responses import StreamingResponse
import magic

from aci.common.db import crud
from aci.common.logging_setup import get_logger
from aci.common.schemas.profiles import UserProfileResponse, UserProfileUpdate
from aci.server import dependencies as deps
from aci.server.file_management import FileManager

logger = get_logger(__name__)
router = APIRouter()


@router.get("", response_model=UserProfileResponse)
def get_my_profile(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
):
    """
    Retrieve the current authenticated user's profile.
    """
    profile = crud.profiles.get_profile(db=context.db_session, user_id=context.user.id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found."
        )
    return profile


@router.put("", response_model=UserProfileResponse)
def update_my_profile(
    profile_update: UserProfileUpdate,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
):
    """
    Update the current authenticated user's profile details, such as their name.
    """
    profile = crud.profiles.update_profile(
        db=context.db_session, user_id=context.user.id, profile_in=profile_update
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found."
        )
    return profile


@router.post("/avatar")
def upload_my_avatar(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
    file: UploadFile = File(...),
):
    """
    Upload or update the current user's avatar.
    Limit: 10MB max, PNG/JPG only (verified by file magic).
    """
    max_size = 10 * 1024 * 1024
    allowed_mime_types = {"image/png", "image/jpeg"}

    # Read file content for size and magic checking
    content = file.file.read()
    
    if len(content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 10MB limit.",
        )

    # Use python-magic to detect actual file type based on file signature
    try:
        detected_mime = magic.from_buffer(content, mime=True)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not determine file type: {str(e)}",
        )

    if detected_mime not in allowed_mime_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only PNG and JPG images are allowed. Detected: {detected_mime}",
        )

    # Reset file pointer to beginning for FileManager
    file.file.seek(0)

    file_manager = FileManager(context.db_session)
    try:
        avatar_path = file_manager.upload_avatar(
            user_id=context.user.id,
            file_object=file.file,
            filename=file.filename or "avatar.png",
        )
        return {"message": "Avatar updated successfully", "avatar_url": avatar_path}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.get("/avatar")
def get_my_avatar(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context)],
):
    """
    Retrieve the current authenticated user's avatar image.
    """
    file_manager = FileManager(context.db_session)
    try:
        url = file_manager.read_avatar(user_id=context.user.id)
        return {"avatar_url": url}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
