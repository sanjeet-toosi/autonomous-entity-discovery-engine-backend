from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MissionCreate(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)
    start: bool = Field(default=False, description="Create and immediately start a run")


class MissionOut(BaseModel):
    id: uuid.UUID
    name: str
    prompt: str
    status: str
    interpretation: dict
    created_at: datetime
    updated_at: datetime
    active_run_id: uuid.UUID | None = None
    active_run_status: str | None = None


class MissionUpdate(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|ARCHIVED)$")
