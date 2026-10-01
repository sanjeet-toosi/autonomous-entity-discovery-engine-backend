"""Central configuration. Every tunable the PRD calls out lives here so workers, API and
scripts share one source of truth. Values come from the environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- service -------------------------------------------------------------------------
    app_name: str = "business-graph-backend"
    environment: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8010
    api_key: str | None = Field(default=None, description="If set, mutating endpoints require X-API-Key")
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001"]

    # --- storage -------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://sanjeet@/business_graph?host=/tmp"
    database_pool_size: int = 10
    redis_url: str = "redis://127.0.0.1:6379/3"

    # Neo4j is the graph *projection*; PostgreSQL stays the system of record.
    neo4j_enabled: bool = True
    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # --- LLM router ----------------------------------------------------------------------
    llm_provider: str = Field(default="auto", description="auto | anthropic | openai | ollama | heuristic")
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b-instruct"
    llm_timeout_seconds: float = 60.0
    llm_max_calls_per_minute: int = 120
    llm_max_daily_spend_usd: float = 25.0

    # --- crawl governance ----------------------------------------------------------------
    crawl_user_agent: str = "BusinessGraphBot/0.1 (+https://github.com/zyra-hq/business-graph; research crawler)"
    crawl_timeout_seconds: float = 15.0
    crawl_max_response_bytes: int = 2_000_000
    crawl_max_redirects: int = 5
    crawl_max_pages_per_domain: int = 4
    crawl_max_depth: int = 1
    crawl_per_domain_rpm: int = 12
    crawl_global_concurrency: int = 32
    crawl_respect_robots: bool = True
    crawl_blocked_domains: list[str] = [
        "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
        "tiktok.com", "pinterest.com", "google.com", "apple.com", "microsoft.com", "wikipedia.org",
        "amazon.com", "reddit.com", "yelp.com", "tripadvisor.com", "doordash.com", "ubereats.com",
        "grubhub.com", "opentable.com", "squarespace.com", "wix.com", "godaddy.com", "wordpress.com",
        "cloudflare.com", "shopify.com", "toasttab.com", "resy.com", "bing.com", "duckduckgo.com",
    ]
    candidate_max_attempts: int = 3
    candidate_lease_seconds: int = 300

    # --- workers -------------------------------------------------------------------------
    worker_heartbeat_seconds: int = 5
    worker_stale_seconds: int = 30
    worker_concurrency: int = 4
    discovery_queue_backpressure: int = 4000
    discovery_search_delay_seconds: float = 4.0
    discovery_max_results_per_query: int = 25

    # --- brain ---------------------------------------------------------------------------
    reflection_interval_seconds: int = 900
    reflection_min_experiences: int = 50

    # --- orchestrator --------------------------------------------------------------------
    orchestrator_tick_seconds: float = 2.0
    metrics_broadcast_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
