from __future__ import annotations

import time

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.llm.interface import Usage

_PRICING = {"gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.5, 10.0), "gpt-4.1-mini": (0.4, 1.6), "gpt-4.1": (2.0, 8.0)}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str | None, model: str, timeout: float):
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=2)

    async def complete_json(self, system: str, user: str, schema: type[BaseModel], *, max_tokens: int, effort: str):
        t0 = time.perf_counter()
        completion = await self._client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=schema,
            max_completion_tokens=max_tokens,
        )
        choice = completion.choices[0]
        if choice.message.refusal:
            raise RuntimeError(f"model refused: {choice.message.refusal}")
        parsed = choice.message.parsed
        if parsed is None:
            raise RuntimeError("no parsed output")
        u = completion.usage
        inp, out = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
        pin, pout = _PRICING.get(self.model, (1.0, 4.0))
        return parsed, Usage(
            self.name, self.model, inp, out, cost_usd=(inp * pin + out * pout) / 1e6,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
