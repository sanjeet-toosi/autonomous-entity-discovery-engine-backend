"""SQLAlchemy 2 models. PostgreSQL is the system of record for every subsystem:
control plane, frontier, knowledge graph, brain, observability."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ======================================================================================
# Control plane
# ======================================================================================
class Mission(TimestampMixin, Base):
    __tablename__ = "missions"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    raw_prompt: Mapped[str] = mapped_column(Text)
    parsed_objective: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    runs: Mapped[list["Run"]] = relationship(back_populates="mission")


class Run(TimestampMixin, Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    strategy_version: Mapped[str | None] = mapped_column(String(100))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mission: Mapped[Mission] = relationship(back_populates="runs")


class RunCheckpoint(Base):
    __tablename__ = "run_checkpoints"
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    reason: Mapped[str] = mapped_column(String(32))
    frontier_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_count: Mapped[int] = mapped_column(Integer, default=0)
    strategy_version: Mapped[str | None] = mapped_column(String(100))
    cursor_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


# ======================================================================================
# Frontier
# ======================================================================================
class CandidateDomain(Base):
    __tablename__ = "candidate_domains"
    __table_args__ = (
        UniqueConstraint("mission_id", "normalized_domain", name="uq_candidate_mission_domain"),
        Index("ix_candidate_claim", "mission_id", "status", "priority"),
        Index("ix_candidate_run_status", "run_id", "status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    domain: Mapped[str] = mapped_column(String(253))
    normalized_domain: Mapped[str] = mapped_column(String(253))
    source_type: Mapped[str] = mapped_column(String(40))
    source_reference: Mapped[str | None] = mapped_column(String(1000))
    priority: Mapped[float] = mapped_column(Float, default=0.5)
    mission_relevance_score: Mapped[float] = mapped_column(Float, default=0.5)
    business_probability: Mapped[float] = mapped_column(Float, default=0.5)
    source_quality_score: Mapped[float] = mapped_column(Float, default=0.5)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.5)
    information_gain_score: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), default="NEW")
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by_worker: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


# ======================================================================================
# Knowledge graph
# ======================================================================================
class BusinessNode(TimestampMixin, Base):
    __tablename__ = "business_nodes"
    id: Mapped[uuid.UUID] = uuid_pk()
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    registrable_domain: Mapped[str] = mapped_column(String(253), index=True)
    name: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    region: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    business_type: Mapped[str] = mapped_column(String(40), default="business")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    primary_category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("category_nodes.id"), index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id"))
    discovered_by_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_reference: Mapped[str | None] = mapped_column(String(1000))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CategoryNode(TimestampMixin, Base):
    __tablename__ = "category_nodes"
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("category_nodes.id"), index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(1000), index=True)  # materialized slug path
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(40), default="agent")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    business_count: Mapped[int] = mapped_column(Integer, default=0)  # direct members
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class GraphEdge(TimestampMixin, Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "target_type", "target_id", "relationship_type", name="uq_edge"),
        Index("ix_edge_target", "target_type", "target_id"),
        Index("ix_edge_source", "source_type", "source_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    source_type: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    relationship_type: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(String(100))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_subject", "subject_type", "subject_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    claim: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    evidence_type: Mapped[str] = mapped_column(String(30))
    evidence_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (Index("ix_pages_domain", "domain"), Index("ix_pages_business", "business_id"))
    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    domain: Mapped[str] = mapped_column(String(253))
    url: Mapped[str] = mapped_column(String(2000))
    final_url: Mapped[str | None] = mapped_column(String(2000))
    status_code: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    structured_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    clean_text: Mapped[str | None] = mapped_column(Text)
    links: Mapped[dict] = mapped_column(JSONB, default=dict)
    crawl_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetch_ms: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (Index("ix_change_created", "created_at"), Index("ix_change_entity", "entity_type", "entity_id"))
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


# ======================================================================================
# Observability
# ======================================================================================
class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (Index("ix_activity_run_created", "run_id", "created_at"), Index("ix_activity_created", "created_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mission_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    level: Mapped[str] = mapped_column(String(10), default="info")
    domain: Mapped[str | None] = mapped_column(String(253))
    message: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    worker_type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), default="HEALTHY")
    hostname: Mapped[str | None] = mapped_column(String(200))
    pid: Mapped[int | None] = mapped_column(Integer)
    current_job: Mapped[dict | None] = mapped_column(JSONB)
    jobs_completed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class MetricHourly(Base):
    """Pre-aggregated counters. scope is 'global', 'mission:<id>' or 'run:<id>'."""
    __tablename__ = "metric_hourly"
    __table_args__ = (UniqueConstraint("bucket", "scope", "metric", name="uq_metric_hourly"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scope: Mapped[str] = mapped_column(String(60), index=True)
    metric: Mapped[str] = mapped_column(String(60))
    value: Mapped[float] = mapped_column(Float, default=0.0)


class ModelInvocation(Base):
    __tablename__ = "model_invocations"
    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    task_type: Mapped[str] = mapped_column(String(40), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    domain: Mapped[str | None] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)


# ======================================================================================
# Brain — what the system has learned about *how* to discover
# ======================================================================================
class BrainMemory(TimestampMixin, Base):
    """Semantic (facts) and procedural (playbooks) memory."""
    __tablename__ = "brain_memories"
    __table_args__ = (UniqueConstraint("memory_type", "vertical", "key", name="uq_brain_memory"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    memory_type: Mapped[str] = mapped_column(String(20))  # semantic | procedural
    vertical: Mapped[str] = mapped_column(String(100), default="*")
    key: Mapped[str] = mapped_column(String(200))
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source: Mapped[str] = mapped_column(String(40), default="seed")
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class BrainExperience(Base):
    __tablename__ = "brain_experiences"
    __table_args__ = (Index("ix_experience_mission_created", "mission_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    mission_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    domain: Mapped[str] = mapped_column(String(253))
    vertical: Mapped[str | None] = mapped_column(String(100))
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_reference: Mapped[str | None] = mapped_column(String(1000))
    strategy_version: Mapped[str | None] = mapped_column(String(100))
    actions: Mapped[list] = mapped_column(JSONB, default=list)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    cost: Mapped[dict] = mapped_column(JSONB, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    reflected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class BrainLesson(TimestampMixin, Base):
    __tablename__ = "brain_lessons"
    id: Mapped[uuid.UUID] = uuid_pk()
    vertical: Mapped[str] = mapped_column(String(100), default="*", index=True)
    title: Mapped[str] = mapped_column(String(300))
    statement: Mapped[str] = mapped_column(Text)
    lesson_type: Mapped[str] = mapped_column(String(40), default="observation")
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    proposed_change: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source: Mapped[str] = mapped_column(String(40), default="reflection")
    experience_count: Mapped[int] = mapped_column(Integer, default=0)


class BrainStrategy(TimestampMixin, Base):
    __tablename__ = "brain_strategies"
    __table_args__ = (UniqueConstraint("vertical", "name", name="uq_strategy_vertical_name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    vertical: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class BrainStrategyVersion(TimestampMixin, Base):
    __tablename__ = "brain_strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brain_strategies.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    rules: Mapped[list] = mapped_column(JSONB, default=list)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    derived_from_lesson_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrainSourceProfile(TimestampMixin, Base):
    __tablename__ = "brain_source_profiles"
    __table_args__ = (UniqueConstraint("source_type", "source", "vertical", name="uq_source_profile"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    source_type: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(300))
    vertical: Mapped[str] = mapped_column(String(100), default="*")
    candidates_seen: Mapped[int] = mapped_column(Integer, default=0)
    candidates_processed: Mapped[int] = mapped_column(Integer, default=0)
    valid_businesses: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    novel_domains: Mapped[int] = mapped_column(Integer, default=0)
    downstream_candidates: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    valid_rate: Mapped[float] = mapped_column(Float, default=0.0)
    duplicate_rate: Mapped[float] = mapped_column(Float, default=0.0)
    novel_domain_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_yield: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.5)


class BrainFailure(TimestampMixin, Base):
    __tablename__ = "brain_failures"
    __table_args__ = (UniqueConstraint("failure_type", "domain", name="uq_failure_type_domain"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    failure_type: Mapped[str] = mapped_column(String(50), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    url: Mapped[str | None] = mapped_column(String(2000))
    detail: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer, default=1)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class BrainExperiment(TimestampMixin, Base):
    __tablename__ = "brain_experiments"
    id: Mapped[uuid.UUID] = uuid_pk()
    vertical: Mapped[str] = mapped_column(String(100))
    hypothesis: Mapped[str] = mapped_column(Text)
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    baseline_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    results: Mapped[dict] = mapped_column(JSONB, default=dict)
    decision: Mapped[str | None] = mapped_column(String(20))


class BrainEvaluation(Base):
    __tablename__ = "brain_evaluations"
    id: Mapped[uuid.UUID] = uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brain_experiments.id"), index=True)
    metric: Mapped[str] = mapped_column(String(60))
    baseline_value: Mapped[float] = mapped_column(Float)
    candidate_value: Mapped[float] = mapped_column(Float)
    delta_pct: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


# ======================================================================================
# Discovery sources: integrations (toggles + keys), structured source records, geo cache
# ======================================================================================
class Integration(Base):
    """Operator-controlled switch for one discovery source. Free sources default on; paid APIs and
    registries run only when enabled here (and, where required, given an API key)."""
    __tablename__ = "integrations"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    api_key: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[dict] = mapped_column(JSONB, default=dict)  # last test result, last use, counters
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now())


class SourceRecord(Base):
    """One listing from a structured source (Google Places, OSM, NPPES...). Kept verbatim with provenance;
    resolved onto a BusinessNode by domain (and later phone/address)."""
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_record"),
        Index("ix_source_records_domain", "website_domain"),
        Index("ix_source_records_mission", "mission_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(300))
    website_domain: Mapped[str | None] = mapped_column(String(253))
    website_url: Mapped[str | None] = mapped_column(String(1000))
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str | None] = mapped_column(String(2))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    categories: Mapped[list] = mapped_column(JSONB, default=list)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    matched_business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_nodes.id"), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class GeoArea(Base):
    """Cached geocoding result (Nominatim) for a mission region: bounding box for grid partitioning and the
    OSM relation id for Overpass area queries."""
    __tablename__ = "geo_areas"
    query: Mapped[str] = mapped_column(String(300), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(500))
    osm_type: Mapped[str | None] = mapped_column(String(20))
    osm_id: Mapped[int | None] = mapped_column(BigInteger)
    bbox: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"south","north","west","east"}
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
