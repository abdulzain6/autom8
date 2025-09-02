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
    description: Optional[str]
    user_id: str
    goal: str
    active: bool
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
    description: Optional[str] = Field(None, description="A brief description of the automation.")
    goal: str = Field(
        ..., description="The specific goal or instruction for the automation."
    )
    is_deep: bool = Field(
        default=False, description="Indicates if the automation is a deep automation.",
    )
    active: bool = Field(default=True, description="Indicates if the automation is active.")
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
            raise ValueError("cron_schedule should only be provided for recurring automations")
        
        if self.cron_schedule:
            if not croniter.is_valid(self.cron_schedule):
                raise ValueError(f"'{self.cron_schedule}' is not a valid cron schedule")

            # Validate the minimum interval ---
            # Calculate the time difference between two consecutive scheduled runs
            # to ensure it's at least 30 minutes.
            cron = croniter(self.cron_schedule)
            next_run = cron.get_next(datetime)
            next_next_run = cron.get_next(datetime)
            
            # 1800 seconds = 30 minutes
            if (next_next_run - next_run).total_seconds() < 1800:
                raise ValueError("Recurring automations cannot be scheduled more frequently than every 30 minutes.")
            # ------------------------------------------

        return self


class AutomationUpdate(BaseModel):
    """Schema to update an existing Automation."""

    name: Optional[str] = Field(None, max_length=255)
    goal: Optional[str] = Field(None, description="A new goal for the automation.")
    linked_account_ids: Optional[list[str]] = Field(None)
    is_recurring: Optional[bool] = None
    cron_schedule: Optional[str] = None
    is_deep: Optional[bool] = None
    active: Optional[bool] = None

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


class AutomationRunResponse(BaseModel):
    """Response schema for triggering an automation run."""
    message: str
    run_id: str


class AutomationAgentCreate(BaseModel):
    """Schema for the voice agent to create automations using app names instead of linked account IDs."""
    
    name: str = Field(..., max_length=255, description="A clear, descriptive name for the automation")
    description: Optional[str] = Field(None, description="A brief description of what this automation does")
    goal: str = Field(..., description="The specific goal or instruction for the automation - what exactly should it accomplish?")
    app_names: List[str] = Field(..., description="List of app names required for this automation (e.g., ['gmail', 'google_calendar', 'notifyme'])")
    is_deep: bool = Field(default=False, description="Set to true for complex automations that require multiple steps")
    active: bool = Field(default=True, description="Whether the automation should be active immediately")
    is_recurring: bool = Field(default=False, description="Whether this automation should run on a schedule")
    cron_schedule: Optional[str] = Field(
        None,
        description="UTC cron schedule (e.g., '0 9 * * 1' for every Monday at 9 AM UTC). Required if is_recurring is true. Minimum interval is 30 minutes."
    )

    @model_validator(mode="after")
    def validate_automation_requirements(self) -> "AutomationAgentCreate":
        # Validate cron schedule requirements
        if self.is_recurring and not self.cron_schedule:
            raise ValueError("cron_schedule is required for recurring automations")
        if not self.is_recurring and self.cron_schedule:
            raise ValueError("cron_schedule should only be provided for recurring automations")
        
        if self.cron_schedule:
            if not croniter.is_valid(self.cron_schedule):
                raise ValueError(f"'{self.cron_schedule}' is not a valid cron schedule")

            # Validate minimum 30-minute interval
            cron = croniter(self.cron_schedule)
            next_run = cron.get_next(datetime)
            next_next_run = cron.get_next(datetime)
            
            if (next_next_run - next_run).total_seconds() < 1800:  # 1800 seconds = 30 minutes
                raise ValueError("Recurring automations cannot be scheduled more frequently than every 30 minutes")

        # Validate app names are provided
        if not self.app_names:
            raise ValueError("At least one app name must be provided")

        return self