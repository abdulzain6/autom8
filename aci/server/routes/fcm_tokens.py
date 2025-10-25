from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status
from aci.common.db import crud
from aci.common.logging_setup import get_logger
from aci.common.schemas.fcm_tokens import FCMTokenUpsert, FCMTokenPublic
from aci.server import dependencies as deps

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "",
    response_model=FCMTokenPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register or Update a Device Token",
)
def upsert_user_fcm_token(
    token_in: FCMTokenUpsert,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    Register a new FCM device token for the authenticated user or update an
    existing one for a specific device type.
    """
    try:
        fcm_token = crud.fcm_tokens.upsert_fcm_token(
            db=context.db_session,
            user_id=context.user.id,
            token_in=token_in,
        )
        return fcm_token
    except Exception as e:
        logger.error(
            f"Failed to upsert FCM token for user {context.user.id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not register the device token.",
        )


@router.get("", response_model=List[FCMTokenPublic], summary="List Device Tokens")
def get_user_fcm_tokens(
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    Retrieve all registered FCM device tokens for the authenticated user.
    """
    tokens = crud.fcm_tokens.get_tokens_for_user(
        db=context.db_session, user_id=context.user.id
    )
    return tokens


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Device Token",
)
def delete_user_fcm_token(
    token_id: str,
    context: Annotated[deps.RequestContext, Depends(deps.get_request_context(check_subscription=False))],
):
    """
    De-register and delete a specific FCM device token for the authenticated user.
    """
    token_to_delete = crud.fcm_tokens.get_token_by_id_and_user(
        db=context.db_session, token_id=token_id, user_id=context.user.id
    )

    if not token_to_delete:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found or you do not have permission to delete it.",
        )

    crud.fcm_tokens.delete_token(db=context.db_session, token=token_to_delete)
    return None
