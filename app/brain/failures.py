from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrainFailure


async def record(session: AsyncSession, failure_type: str, domain: str, detail: str | None, url: str | None = None, run_id=None) -> None:
    stmt = insert(BrainFailure).values(failure_type=failure_type[:50], domain=domain, url=(url or "")[:2000] or None, detail=(detail or "")[:1000],
                                       run_id=uuid.UUID(str(run_id)) if run_id else None)
    stmt = stmt.on_conflict_do_update(constraint="uq_failure_type_domain", set_={"count": BrainFailure.count + 1, "detail": (detail or "")[:1000]})
    await session.execute(stmt)
