from fastapi import APIRouter

from app.api import activity, brain, businesses, categories, graph, integrations, missions, runs, stats, system, ws

api_router = APIRouter()
v1 = APIRouter(prefix="/api/v1")
for r in (missions.router, runs.router, graph.router, businesses.router, categories.router, brain.router, stats.router, activity.router, system.router, integrations.router):
    v1.include_router(r)
api_router.include_router(v1)
api_router.include_router(ws.router)
