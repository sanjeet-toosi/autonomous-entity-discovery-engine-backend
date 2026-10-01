"""Discovery sources are *sensors*: each turns a mission into a deterministic list of jobs, executes one job
at a time (so pause/resume never repeats work) and returns listings. The worker owns persistence, scoring
and the frontier; sources only talk to the outside world."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.service import EffectiveIntegration


@dataclass
class SourceHit:
    source: str
    external_id: str
    name: str | None = None
    website_url: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    categories: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    structured: bool = True  # False for web search results (a URL + snippet, not a listing)
    snippet: str = ""


@dataclass
class JobResult:
    hits: list[SourceHit]
    follow_up_jobs: list[dict] = field(default_factory=list)  # e.g. split cells, next pages
    note: str | None = None


class DiscoverySource(Protocol):
    key: str
    min_interval_seconds: int  # pacing between two jobs of this source for one run

    def applies(self, parsed_objective: dict) -> tuple[bool, str]:
        """Whether this source can serve the mission (vertical/country), with a human reason."""

    async def plan(self, session: AsyncSession, parsed_objective: dict, strategy_config: dict, integration: EffectiveIntegration) -> list[dict]:
        """Deterministic job list for the mission. Index into it is the resumable cursor."""

    async def execute(self, job: dict, integration: EffectiveIntegration, stats: dict) -> JobResult:
        """Run one job."""

    async def test(self, integration: EffectiveIntegration) -> dict:
        """Cheap connectivity/credential probe for the UI."""

    def describe(self, job: dict) -> str:
        """Short label for activity events (used as candidate source_reference)."""
