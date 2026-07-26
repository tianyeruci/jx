from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class BusinessChatRequest(BaseModel):
    query: str = Field(min_length=1)
    conversation_id: str | None = None


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
    intent: Literal["general_qa", "direct_course", "route_recommendation", "clarification"] = "general_qa"
    candidate_course_ids: list[str] = Field(default_factory=list)
    candidate_route_id: str = ""
    missing_information: list[str] = Field(default_factory=list, max_length=2)
    clarification_question: str = ""
    answer_plan: str = ""
    expert_insight: str = ""
    progress_action: dict[str, Any] = Field(default_factory=dict)
