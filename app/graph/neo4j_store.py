"""Neo4j projection of the knowledge graph. PostgreSQL remains authoritative; this store mirrors
mutations so traversal / visualization can use a native graph engine. All calls are best-effort."""
from __future__ import annotations

from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.core.logging import get_logger

log = get_logger("neo4j")


class Neo4jStore:
    def __init__(self) -> None:
        s = get_settings()
        self.enabled = s.neo4j_enabled and bool(s.neo4j_password)
        self._db = s.neo4j_database
        self._driver = AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)) if self.enabled else None
        self._schema_ready = False

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()

    async def ensure_schema(self) -> None:
        if not self.enabled or self._schema_ready:
            return
        stmts = [
            "CREATE CONSTRAINT business_domain IF NOT EXISTS FOR (b:Business) REQUIRE b.domain IS UNIQUE",
            "CREATE CONSTRAINT category_slug IF NOT EXISTS FOR (c:Category) REQUIRE c.slug IS UNIQUE",
            "CREATE INDEX business_country IF NOT EXISTS FOR (b:Business) ON (b.country_code)",
        ]
        try:
            async with self._driver.session(database=self._db) as s:
                for st in stmts:
                    await s.run(st)
            self._schema_ready = True
        except Exception as e:
            log.warning("neo4j schema setup failed", extra={"extra": {"error": str(e)}})

    async def healthy(self) -> bool:
        if not self.enabled:
            return False
        try:
            async with self._driver.session(database=self._db) as s:
                await s.run("RETURN 1")
            return True
        except Exception:
            return False

    async def upsert_business(self, b: dict, categories: list[dict], edge_confidence: float) -> None:
        if not self.enabled:
            return
        await self.ensure_schema()
        q = """
        MERGE (b:Business {domain: $domain})
        SET b += $props, b.updated_at = datetime()
        WITH b
        UNWIND $categories AS c
          MERGE (cat:Category {slug: c.slug}) SET cat.name = c.name, cat.path = c.path, cat.depth = c.depth
          WITH b, cat, c
          OPTIONAL MATCH (parent:Category {slug: c.parent_slug})
          FOREACH (_ IN CASE WHEN parent IS NULL THEN [] ELSE [1] END |
            MERGE (cat)-[:SUBCATEGORY_OF]->(parent))
          FOREACH (_ IN CASE WHEN c.leaf THEN [1] ELSE [] END |
            MERGE (b)-[r:BELONGS_TO]->(cat) SET r.confidence = $conf, r.updated_at = datetime())
        """
        try:
            async with self._driver.session(database=self._db) as s:
                await s.run(q, domain=b["domain"], props=b, categories=categories, conf=edge_confidence)
        except Exception as e:
            log.warning("neo4j upsert failed", extra={"extra": {"error": str(e), "domain": b.get("domain")}})

    async def link_businesses(self, source_domain: str, target_domains: list[str]) -> None:
        if not self.enabled or not target_domains:
            return
        q = """
        MATCH (a:Business {domain: $src})
        UNWIND $targets AS t
          MATCH (b:Business {domain: t})
          MERGE (a)-[r:LINKS_TO]->(b) ON CREATE SET r.created_at = datetime()
        """
        try:
            async with self._driver.session(database=self._db) as s:
                await s.run(q, src=source_domain, targets=target_domains)
        except Exception as e:
            log.warning("neo4j link failed", extra={"extra": {"error": str(e)}})

    async def neighborhood(self, domain: str, limit: int = 50) -> dict:
        if not self.enabled:
            return {"nodes": [], "edges": []}
        q = """
        MATCH (b:Business {domain: $domain})
        OPTIONAL MATCH (b)-[r]-(n)
        WITH b, collect({rel: type(r), node: n, out: startNode(r) = b})[0..$limit] AS rels
        RETURN b, rels
        """
        try:
            async with self._driver.session(database=self._db) as s:
                rec = await (await s.run(q, domain=domain, limit=limit)).single()
        except Exception as e:
            log.warning("neo4j neighborhood failed", extra={"extra": {"error": str(e)}})
            return {"nodes": [], "edges": []}
        if not rec:
            return {"nodes": [], "edges": []}
        b = rec["b"]
        nodes = [{"id": b["domain"], "label": b.get("name") or b["domain"], "type": "business"}]
        edges = []
        for item in rec["rels"]:
            n = item["node"]
            if n is None:
                continue
            nid = n.get("domain") or n.get("slug")
            nodes.append({"id": nid, "label": n.get("name") or nid, "type": "business" if "domain" in n else "category"})
            edges.append({"source": b["domain"] if item["out"] else nid, "target": nid if item["out"] else b["domain"], "type": item["rel"]})
        return {"nodes": nodes, "edges": edges}


_store: Neo4jStore | None = None


def get_neo4j() -> Neo4jStore:
    global _store
    if _store is None:
        _store = Neo4jStore()
    return _store
