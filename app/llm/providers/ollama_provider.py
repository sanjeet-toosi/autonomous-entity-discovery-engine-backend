"""Local models through Ollama's /api/chat with JSON-schema constrained output."""
from __future__ import annotations

import time

import httpx
from pydantic import BaseModel

from app.llm.interface import Usage


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float):
        self.model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def complete_json(self, system: str, user: str, schema: type[BaseModel], *, max_tokens: int, effort: str):
        t0 = time.perf_counter()
        body = {
            "model": self.model,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(f"{self._base}/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
        parsed = schema.model_validate_json(data["message"]["content"])
        return parsed, Usage(
            self.name, self.model, data.get("prompt_eval_count", 0), data.get("eval_count", 0),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                r = await client.get(f"{self._base}/api/tags")
                return r.status_code == 200 and any(m["name"] == self.model for m in r.json().get("models", []))
        except Exception:
            return False
