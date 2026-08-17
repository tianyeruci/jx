from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class BusinessChatRequest(BaseModel):
    query: str = Field(min_length=1)
    conversation_id: str | None = None


class CursorPage(BaseModel):
    next_cursor: str | None = None


class RetentionSettingsRequest(BaseModel):
    history_retention_days: int = Field(ge=1, le=365)


class StyleSettingUpdateRequest(BaseModel):
    enabled: bool
    style_prompt: str = Field(default="", max_length=4000)


class DocumentStatusRequest(BaseModel):
    enabled: bool


class DeleteDocumentRequest(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=500)


class CleaningReviewDecisionRequest(BaseModel):
    action: Literal["approve", "replace_and_approve", "exclude"]
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="", max_length=1000)
    metadata_patch: dict[str, Any] = Field(default_factory=dict)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class FeedbackRequest(BaseModel):
    rating: Literal["like", "dislike"] | None
    content: str = Field(default="", max_length=1000)


class MomentsInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=12000)
    source_material: str = Field(default="", max_length=24000)
    audience: str = Field(default="", max_length=500)
    tone: str = Field(default="", max_length=500)
    constraints: str = Field(default="", max_length=3000)


class MomentsRunRequest(BaseModel):
    inputs: MomentsInputs


class WritingInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=12000)
    source_text: str = Field(default="", max_length=30000)
    audience: str = Field(default="", max_length=500)
    tone: str = Field(default="", max_length=500)
    format: str = Field(default="", max_length=500)
    constraints: str = Field(default="", max_length=3000)


class WritingRunRequest(BaseModel):
    inputs: WritingInputs


class ToolExampleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    request: str = Field(min_length=1, max_length=12000)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)


class ToolExamplePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    request: str | None = Field(default=None, min_length=1, max_length=12000)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)


class CourseRecord(BaseModel):
    course_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    name: str = Field(min_length=1, max_length=200)
    direction: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    official_url: HttpUrl
    enabled: bool = True
    review_status: Literal["approved", "pending_review", "rejected"] = "approved"
    # `stage` is a catalogue-wide difficulty level.  It is deliberately
    # separate from a route stage, which only describes a course's position
    # within that one route.
    stage: str = ""
    source_platform: str = "小鹅通"
    series_name: str | None = None
    published_at: date | None = None
    prerequisite_course_ids: list[str] = Field(default_factory=list)
    audience: str = ""
    highlights: list[str] = Field(default_factory=list)
    learning_outcomes: list[str] = Field(default_factory=list)
    special_prerequisites: list[str] = Field(default_factory=list)
    stage_tags: list[str] = Field(default_factory=list)


class RouteStage(BaseModel):
    stage_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    name: str = Field(min_length=1, max_length=100)
    order: int = Field(ge=1)
    course_ids: list[str] = Field(min_length=1)


class RouteRecord(BaseModel):
    route_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    name: str = Field(min_length=1, max_length=200)
    direction: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1)
    enabled: bool = True
    review_status: Literal["approved", "pending_review", "rejected"] = "approved"
    stages: list[RouteStage] = Field(min_length=1)


class RecommendationDecision(BaseModel):
    intent: Literal["general_qa", "direct_course", "route_recommendation", "clarification"] = (
        "general_qa"
    )
    candidate_course_ids: list[str] = Field(default_factory=list)
    candidate_route_id: str = ""
    missing_information: list[str] = Field(default_factory=list, max_length=2)
    clarification_question: str = ""
    answer_plan: str = ""
    progress_action: dict[str, Any] = Field(default_factory=dict)
    # Internal outputs from the main Dify answer node. These are validated
    # against the final server-side course cards and never exposed as a new
    # HTTP/SSE event.
    final_answer: str = ""
    expert_insight: str = Field(default="", max_length=2000)
    # A route stage can legitimately contain more than five courses.  The
    # cardinality limit belongs to direct recommendations, not this internal
    # integrity list which must exactly mirror the final card batch.
    expert_course_ids: list[str] = Field(default_factory=list)
