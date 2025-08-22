from __future__ import annotations
from typing import Optional, List, Dict, Any
from datetime import datetime
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aci.common.db.sql_models import Automation
from aci.common.enums import RunStatus, SecurityScheme


class LinkedAccountForAutomationPublic(BaseModel):
    """A slim representation of a LinkedAccount for use within an Automation response."""

    id: str
    app_name: str
    security_scheme: SecurityScheme

    model_config = ConfigDict(from_attributes=True)


class AutomationPublic(BaseModel):
    """The public representation of an Automation."""

    id: str
    name: str
    user_id: str
    goal: str
    last_run_at: Optional[datetime]
    is_recurring: bool
    cron_schedule: Optional[str] = None
    last_run_status: RunStatus
    created_at: datetime
    updated_at: datetime
    linked_accounts: list[LinkedAccountForAutomationPublic]
    is_deep: bool
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def flatten_linked_accounts(cls, data: Any) -> Any:
        """
        Transforms the nested ORM relationship for Pydantic.
        Automation -> AutomationLinkedAccount -> LinkedAccount becomes a direct list.
        """
        if not isinstance(data, Automation):
            return data

        automation_dict = {
            field_name: getattr(data, field_name)
            for field_name in cls.model_fields.keys()
            if hasattr(data, field_name)
        }

        if "linked_accounts" in automation_dict:
            automation_dict["linked_accounts"] = [
                assoc.linked_account for assoc in data.linked_accounts
            ]

        return automation_dict


class AutomationCreate(BaseModel):
    """Schema to create a new Automation directly."""

    name: str = Field(..., max_length=255)
    goal: str = Field(
        ..., description="The specific goal or instruction for the automation."
    )
    is_deep: bool = Field(
        default=False, description="Indicates if the automation is a deep automation.",
    )
    linked_account_ids: list[str] = Field(default_factory=list)
    is_recurring: bool = Field(default=False)
    cron_schedule: Optional[str] = Field(
        None,
        description="A valid cron schedule (e.g., '0 5 * * *'). Required if is_recurring is true.",
    )

    @model_validator(mode="after")
    def check_cron_schedule(self) -> "AutomationCreate":
        if self.is_recurring and not self.cron_schedule:
            raise ValueError("cron_schedule is required for recurring automations")
        if not self.is_recurring and self.cron_schedule:
            raise ValueError(
                "cron_schedule should only be provided for recurring automations"
            )
        if self.cron_schedule and not croniter.is_valid(self.cron_schedule):
            raise ValueError(f"'{self.cron_schedule}' is not a valid cron schedule")
        return self


class AutomationUpdate(BaseModel):
    """Schema to update an existing Automation."""

    name: Optional[str] = Field(None, max_length=255)
    goal: Optional[str] = Field(None, description="A new goal for the automation.")
    linked_account_ids: Optional[list[str]] = Field(None)
    is_recurring: Optional[bool] = None
    cron_schedule: Optional[str] = None
    is_deep: Optional[bool] = None

    @model_validator(mode="after")
    def check_cron_format(self) -> "AutomationUpdate":
        if self.cron_schedule and not croniter.is_valid(self.cron_schedule):
            raise ValueError(f"'{self.cron_schedule}' is not a valid cron schedule")
        return self


class AutomationFromTemplateCreate(BaseModel):
    """Schema for creating a new automation from a template."""

    template_id: str = Field(
        ..., description="The ID of the automation template to use."
    )
    name: str = Field(
        ..., max_length=255, description="A unique name for the new automation."
    )
    variables: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value pairs for the template's Jinja2 variables.",
    )
    linked_account_ids: List[str] = Field(
        ...,
        description="A list of the user's LinkedAccount IDs to be used for this automation.",
    )
    is_recurring: bool = Field(default=False)
    cron_schedule: Optional[str] = Field(
        None, description="A valid cron schedule. Required if is_recurring is true."
    )
    is_deep: Optional[bool] = Field(
        None, description="Indicates if the automation is a deep automation."
    )

    @model_validator(mode="after")
    def check_cron_schedule(self) -> "AutomationFromTemplateCreate":
        if self.is_recurring and not self.cron_schedule:
            raise ValueError("cron_schedule is required for recurring automations")
        if not self.is_recurring and self.cron_schedule:
            raise ValueError(
                "cron_schedule should only be provided for recurring automations"
            )
        if self.cron_schedule and not croniter.is_valid(self.cron_schedule):
            raise ValueError(f"'{self.cron_schedule}' is not a valid cron schedule")
        return self


class AutomationListParams(BaseModel):
    """Query parameters for listing automations."""

    status: Optional[RunStatus] = Field(default=None)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
