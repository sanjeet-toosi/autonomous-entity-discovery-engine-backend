"""Model abstraction. Agent components call *tasks* (plan_mission, extract_business, reflect...);
the router decides which provider executes them. Providers implement `complete_json`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T", bound=BaseModel)


def _normalize_confidence(v):
    """Small models often emit confidence as a percentage (95) or a 0-10 score (9) instead of 0.0-1.0.
    Rescale instead of failing validation, which would silently drop the whole extraction to the heuristic fallback."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f > 1.0:
        f = f / 10.0 if f <= 10.0 else f / 100.0
    return max(0.0, min(1.0, f))



@dataclass
class Usage:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    extra: dict = field(default_factory=dict)


class Provider(Protocol):
    name: str
    model: str

    async def complete_json(self, system: str, user: str, schema: type[T], *, max_tokens: int, effort: str) -> tuple[T, Usage]: ...


# ---------------------------------------------------------------------------------------
# Task schemas — the contract between agents and models
# ---------------------------------------------------------------------------------------
class GeographySpec(BaseModel):
    country: str | None = Field(default=None, description="ISO-3166 alpha-2, e.g. US")
    regions: list[str] = Field(default_factory=list, description="States / provinces if the user named any")
    cities: list[str] = Field(default_factory=list)


class MissionPlan(BaseModel):
    entity_type: str = "business"
    vertical: str = Field(description="Primary vertical, singular lowercase, e.g. 'restaurant'")
    related_verticals: list[str] = Field(default_factory=list)
    category_root: str = Field(description="Top-level taxonomy branch, e.g. 'Food & Beverage'")
    geography: GeographySpec = Field(default_factory=GeographySpec)
    discovery_mode: str = "continuous"
    objective: str = "maximize unique valid business domains"
    completion_strategy: str = "saturation"
    keywords: list[str] = Field(default_factory=list, description="Signals that a page belongs to the vertical")
    summary: str = Field(description="One-sentence human readable interpretation")


class BusinessExtraction(BaseModel):
    is_business: bool = Field(description="True only if the site represents a single commercial entity (or brand)")
    entity_kind: str = Field(description="business | directory | aggregator | association | media | parked | personal | government | other")
    name: str | None = None
    description: str | None = Field(default=None, description="<= 2 sentences, factual")
    category_path: list[str] = Field(
        default_factory=list,
        description="Taxonomy path from broad to specific, e.g. ['Food & Beverage','Restaurant','Italian Restaurant']. 2-3 levels.",
    )
    matches_vertical: bool = Field(description="Whether the business belongs to the mission vertical")
    country: str | None = Field(default=None, description="ISO alpha-2")
    region: str | None = Field(default=None, description="State / province name")
    city: str | None = None
    confidence: float = Field(ge=0, le=1, description="Decimal between 0.0 and 1.0 (e.g. 0.85). Never a percentage.")
    evidence: list[str] = Field(default_factory=list, description="Short quotes / signals that justify the answer")
    is_directory_of_vertical: bool = Field(default=False, description="True if page lists many businesses of the vertical")

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        return _normalize_confidence(v)


class LessonProposal(BaseModel):
    title: str
    statement: str
    lesson_type: str = Field(description="source_quality | crawl_pattern | classification | failure | taxonomy")
    confidence: float = Field(ge=0, le=1, description="Decimal between 0.0 and 1.0 (e.g. 0.85). Never a percentage.")
    proposed_change: dict | None = Field(default=None, description="Optional strategy configuration delta")

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        return _normalize_confidence(v)


class ReflectionOutput(BaseModel):
    summary: str
    lessons: list[LessonProposal] = Field(default_factory=list)
