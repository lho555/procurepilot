from typing import Literal

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.routes import router
from app.knowledge import KnowledgeError
from app.knowledge_routes import router as knowledge_router
from app.agent_routes import router as agent_router
from app.approval import router as approval_router
from app.workflow import router as workflow_router

app = FastAPI(title="ProcurePilot AI", version="0.6.0")
app.include_router(router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(approval_router)
app.include_router(workflow_router)


@app.exception_handler(KnowledgeError)
async def knowledge_error(_request: Request, _error: KnowledgeError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "知识索引不可用，请先完成文档导入"})


@app.exception_handler(SQLAlchemyError)
async def database_error(_request: Request, error: SQLAlchemyError) -> JSONResponse:
    # Log type only: SQL errors may contain connection strings or request data.
    logging.getLogger(__name__).warning("Database failure: %s", type(error).__name__)
    return JSONResponse(status_code=503, content={"detail": "数据库不可用，请检查连接并执行迁移"})


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "procurepilot-api"
    stage: int = 0


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Keep the Stage 0 liveness contract; no database access."""
    return HealthResponse()
