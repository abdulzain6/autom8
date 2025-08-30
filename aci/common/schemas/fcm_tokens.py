from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from aci.common.enums import DeviceType


class FCMTokenUpsert(BaseModel):
    """
    Schema for creating or updating (upserting) an FCM device token.
    """

    token: str = Field(..., description="The FCM device registration token.")
    device_type: DeviceType = Field(
        ..., description="The type of the device (ios, android, web)."
    )


class FCMTokenPublic(BaseModel):
    """
    Public representation of a stored FCM token.
    """

    id: str
    user_id: str
    token: str
    device_type: DeviceType
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
