from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent import AgentRuntime
from app.mcp_tools import MCPReadTools
from app.knowledge import get_knowledge_store
from app.model_explanation import ConfiguredAdapter

router = APIRouter(prefix="/agent", tags=["agent"])


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: Annotated[str, Field(min_length=2, max_length=1000)]


def get_agent_runtime() -> AgentRuntime:
    return AgentRuntime(MCPReadTools(get_knowledge_store()), ConfiguredAdapter())


@router.post("/analyze")
async def analyze(body: AnalyzeRequest, runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)]):
    return await runtime.run(body.query)
