from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    mode: str
    intent: str
    tool_results: list
    final_response: str
    order_step: str
    order_draft: dict[str, Any]
    kooperatif_id: int | None
