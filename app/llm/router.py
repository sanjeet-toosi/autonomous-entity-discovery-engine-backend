"""LLM router: picks a provider, enforces rate/spend limits, records every invocation with cost,
and always degrades to the heuristic provider so the discovery loop never blocks on a model."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select

from app.config import get_settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.db.models import ModelInvocation
from app.llm.interface import BusinessExtraction, MissionPlan, ReflectionOutput, Usage
from app.llm.prompts import EXTRACT_SYSTEM, PLAN_MISSION_SYSTEM, REFLECT_SYSTEM
from app.llm.providers.heuristic import HeuristicProvider
from app.metrics import service as metrics

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)


class _RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._stamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._stamps = [s for s in self._stamps if now - s < 60]
                if len(self._stamps) < self.per_minute:
                    self._stamps.append(now)
                    return
                wait = 60 - (now - self._stamps[0])
            await asyncio.sleep(max(0.05, wait))


class LLMRouter:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.heuristic = HeuristicProvider()
        self.primary = self._build_primary()
        self._limiter = _RateLimiter(s.llm_max_calls_per_minute)
        self._spend_cache: tuple[datetime, float] | None = None

    def _build_primary(self):
        s = self.settings
        choice = s.llm_provider
        if choice == "auto":
            if s.anthropic_api_key:
                choice = "anthropic"
            elif s.openai_api_key:
                choice = "openai"
            else:
                choice = "ollama"
        if choice == "anthropic":
            from app.llm.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(s.anthropic_api_key, s.anthropic_model, s.llm_timeout_seconds)
        if choice == "openai":
            from app.llm.providers.openai_provider import OpenAIProvider
            return OpenAIProvider(s.openai_api_key, s.openai_model, s.llm_timeout_seconds)
        if choice == "ollama":
            from app.llm.providers.ollama_provider import OllamaProvider
            return OllamaProvider(s.ollama_base_url, s.ollama_model, s.llm_timeout_seconds)
        return None  # heuristic only

    @property
    def provider_name(self) -> str:
        return self.primary.name if self.primary else "heuristic"

    @property
    def model_name(self) -> str:
        return self.primary.model if self.primary else self.heuristic.model

    # ----------------------------------------------------------------------------------
    async def _daily_spend(self) -> float:
        now = datetime.now(timezone.utc)
        if self._spend_cache and now - self._spend_cache[0] < timedelta(seconds=60):
            return self._spend_cache[1]
        async with session_scope() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(ModelInvocation.cost_usd), 0.0)).where(
                    ModelInvocation.created_at >= now - timedelta(hours=24)
                )
            )
        self._spend_cache = (now, float(total or 0))
        return self._spend_cache[1]

    async def _record(self, task: str, usage: Usage, ok: bool, error: str | None, run_id, domain) -> None:
        try:
            async with session_scope() as session:
                session.add(
                    ModelInvocation(
                        provider=usage.provider, model=usage.model, task_type=task,
                        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                        cost_usd=usage.cost_usd, latency_ms=usage.latency_ms, success=ok, error=error,
                        run_id=uuid.UUID(str(run_id)) if run_id else None, domain=domain,
                    )
                )
                if usage.provider != "heuristic":
                    await metrics.incr_many(session, {metrics.LLM_CALLS: 1, metrics.LLM_COST_USD: usage.cost_usd}, run_id=run_id)
        except Exception as e:
            log.warning("could not record model invocation", extra={"extra": {"error": str(e)}})

    async def _run(self, task: str, system: str, payload: dict, schema: type[T], *, max_tokens: int, effort: str,
                   run_id=None, domain=None) -> tuple[T, str]:
        user = json.dumps(payload, ensure_ascii=False, default=str)
        if self.primary is not None:
            if await self._daily_spend() < self.settings.llm_max_daily_spend_usd:
                await self._limiter.acquire()
                try:
                    out, usage = await self.primary.complete_json(system, user, schema, max_tokens=max_tokens, effort=effort)
                    await self._record(task, usage, True, None, run_id, domain)
                    return out, usage.provider
                except Exception as e:
                    log.warning("primary provider failed, falling back", extra={"extra": {"task": task, "error": str(e)[:300]}})
                    await self._record(task, Usage(self.primary.name, self.primary.model), False, str(e)[:500], run_id, domain)
            else:
                log.warning("daily LLM spend cap reached; using heuristic provider")
        out, usage = await self.heuristic.complete_json(system, user, schema, max_tokens=max_tokens, effort=effort)
        return out, usage.provider

    # ---- tasks ------------------------------------------------------------------------
    async def plan_mission(self, prompt: str) -> tuple[MissionPlan, str]:
        return await self._run("plan_mission", PLAN_MISSION_SYSTEM, {"prompt": prompt}, MissionPlan, max_tokens=1500, effort="high")

    async def extract_business(self, page: dict, mission: dict, *, run_id=None, domain=None) -> tuple[BusinessExtraction, str]:
        return await self._run(
            "extract_business", EXTRACT_SYSTEM, {"mission": mission, "page": page}, BusinessExtraction,
            max_tokens=1200, effort="low", run_id=run_id, domain=domain,
        )

    async def reflect(self, dataset: dict, *, run_id=None) -> tuple[ReflectionOutput, str]:
        return await self._run("reflect", REFLECT_SYSTEM, dataset, ReflectionOutput, max_tokens=4000, effort="high", run_id=run_id)


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
