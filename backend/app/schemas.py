from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Role = Literal["student", "lead", "teacher"]
TaskStatus = Literal["todo", "in_progress", "in_review", "done"]
Priority = Literal["low", "medium", "high"]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class UserView(Schema):
    id: UUID
    display_name: str
    group_name: str | None
    timezone: str


class SessionView(Schema):
    user: UserView
    csrf_token: str
    expires_at: datetime


class ProjectCreate(Schema):
    title: ShortText
    description: LongText
    goal: LongText
    expected_result: LongText
    due_at: AwareDatetime

    @field_validator("due_at", mode="before")
    @classmethod
    def require_rfc3339(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("RFC3339 timestamp with timezone offset required")
        if isinstance(value, str):
            # Pydantic also accepts Unix timestamps encoded as strings.
            # Parse the date form and offset before Pydantic sees the value.
            try:
                parsed = datetime.fromisoformat(value.upper())
                if parsed.tzinfo is None:
                    raise ValueError("Timezone offset required")
            except ValueError:
                raise ValueError("RFC3339 timestamp with timezone offset required") from None
        return value


class ProjectView(ProjectCreate):
    id: UUID
    status: Literal["active", "final_review", "changes_requested", "completed"]
    current_role: Role
    version: int
    created_at: datetime
    updated_at: datetime


class MemberView(Schema):
    id: UUID
    project_id: UUID
    user: UserView
    role: Role
    active: bool
    joined_at: datetime
    left_at: datetime | None


class InvitationCreate(Schema):
    role: Literal["student"]


class InvitationView(Schema):
    id: UUID
    project_id: UUID
    role: Literal["student", "teacher"]
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


class InvitationCreated(Schema):
    invitation: InvitationView
    invite_url: str


class InvitationPreview(Schema):
    project_title: str
    role: Literal["student", "teacher"]
    expires_at: datetime
    requires_login: bool


class TaskCreate(Schema):
    title: ShortText
    description: LongText
    expected_result: LongText
    due_at: AwareDatetime
    priority: Priority

    _timestamp = field_validator("due_at", mode="before")(ProjectCreate.require_rfc3339.__func__)


class AssigneeUpdate(Schema):
    assignee_member_id: UUID | None


class TaskView(TaskCreate):
    id: UUID
    project_id: UUID
    assignee_member_id: UUID | None
    status: TaskStatus
    version: int
    is_overdue: bool
    is_due_soon: bool
    created_at: datetime
    updated_at: datetime


T = TypeVar("T")


class Page(Schema, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
