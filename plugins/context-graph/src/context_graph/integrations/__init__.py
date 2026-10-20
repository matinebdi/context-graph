from context_graph.integrations.base import ClientIntegration, JsonConfig, PythonRuntime
from context_graph.integrations.claude_code import ClaudeCodeIntegration
from context_graph.integrations.cursor import CursorIntegration

INTEGRATIONS: dict[str, type[ClientIntegration]] = {
    integration.NAME: integration for integration in (ClaudeCodeIntegration, CursorIntegration)
}

__all__ = [
    "INTEGRATIONS", "ClaudeCodeIntegration", "ClientIntegration", "CursorIntegration",
    "JsonConfig", "PythonRuntime",
]
