"""Read/write integration state and expose the *effective* configuration (catalog defaults merged with
operator overrides) to the discovery worker and the API."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Integration
from app.integrations.catalog import SOURCE_CATALOG


@dataclass
class EffectiveIntegration:
    key: str
    enabled: bool
    api_key: str | None
    config: dict
    status: dict
    catalog: dict

    @property
    def usable(self) -> bool:
        return self.enabled and (bool(self.api_key) or not self.catalog["requires_key"])


def _defaults(key: str) -> dict:
    return {f["key"]: f["default"] for f in SOURCE_CATALOG[key].get("config_fields", [])}


async def effective(session: AsyncSession) -> dict[str, EffectiveIntegration]:
    rows = {r.key: r for r in (await session.scalars(select(Integration))).all()}
    out: dict[str, EffectiveIntegration] = {}
    for key, cat in SOURCE_CATALOG.items():
        row = rows.get(key)
        out[key] = EffectiveIntegration(
            key=key, enabled=row.enabled if row else cat["default_enabled"], api_key=row.api_key if row else None,
            config={**_defaults(key), **((row.config if row else None) or {})}, status=(row.status if row else None) or {}, catalog=cat,
        )
    return out


def mask(key: str | None) -> str | None:
    if not key:
        return None
    return key[:4] + "…" + key[-4:] if len(key) > 10 else "•" * len(key)


def public_view(e: EffectiveIntegration) -> dict:
    return {"key": e.key, "label": e.catalog["label"], "kind": e.catalog["kind"], "description": e.catalog["description"],
            "docs_url": e.catalog["docs_url"], "requires_key": e.catalog["requires_key"], "config_fields": e.catalog["config_fields"],
            "enabled": e.enabled, "usable": e.usable, "has_key": bool(e.api_key), "api_key_masked": mask(e.api_key), "config": e.config, "status": e.status}


async def update(session: AsyncSession, key: str, *, enabled: bool | None = None, api_key: str | None = None, clear_key: bool = False,
                 config: dict | None = None) -> EffectiveIntegration:
    if key not in SOURCE_CATALOG:
        raise KeyError(key)
    row = await session.get(Integration, key)
    if row is None:
        row = Integration(key=key, enabled=SOURCE_CATALOG[key]["default_enabled"], config={}, status={})
        session.add(row)
    if enabled is not None:
        row.enabled = enabled
    if clear_key:
        row.api_key = None
    elif api_key:
        row.api_key = api_key.strip()
    if config is not None:
        row.config = {**(row.config or {}), **config}
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return (await effective(session))[key]


async def record_status(session: AsyncSession, key: str, patch: dict) -> None:
    row = await session.get(Integration, key)
    if row is None:
        row = Integration(key=key, enabled=SOURCE_CATALOG[key]["default_enabled"], config={}, status={})
        session.add(row)
    row.status = {**(row.status or {}), **patch}
    await session.flush()
