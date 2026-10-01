from __future__ import annotations

import time

import anthropic
from pydantic import BaseModel

from app.llm.interface import Usage

# USD per 1M tokens (input, output). Used for cost tracking only.
_PRICING = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str, timeout: float):
        self.model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)

    async def complete_json(self, system: str, user: str, schema: type[BaseModel], *, max_tokens: int, effort: str):
        t0 = time.perf_counter()
        response = await self._client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": effort},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        if response.parsed_output is None:
            raise RuntimeError(f"no parsed output (stop_reason={response.stop_reason})")
        inp, out = response.usage.input_tokens, response.usage.output_tokens
        pin, pout = _PRICING.get(self.model, (5.0, 25.0))
        usage = Usage(
            self.name, self.model, inp, out, cost_usd=(inp * pin + out * pout) / 1e6,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        return response.parsed_output, usage
